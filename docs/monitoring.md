# Monitoring and long-term storage

[kube-prometheus-stack](../platform/kube-prometheus-stack/resources.yaml) deploys Prometheus, Alertmanager, Grafana, and metrics collectors. [Thanos](../platform/thanos/resources.yaml) provides long-term metric storage and a single query endpoint.

## Metric storage and queries

Prometheus retains local data for up to 3 days, with an 8 GB size limit. Cleanup starts when either limit is reached. The Sidecar uploads completed TSDB blocks to the `thanos/cloudlab/` prefix in COS. Retention is `0s` for raw, 5-minute, and 1-hour data, so Thanos does not delete these blocks based on age. COS lifecycle rules must not expire current metric blocks based on age either.

Grafana has one Prometheus-type data source, named **Prometheus**, with UID `prometheus`, pointing to Thanos Query. Query reads Prometheus through the Sidecar and COS through the Store Gateway, then combines the results. Historical data is not written back into Prometheus. The official Health Auto Export dashboards query MongoDB through an HTTP API and do not use Thanos.

The Compactor merges blocks and creates downsampled data. Query enables `--query.auto-downsampling` to choose raw, 5-minute, or 1-hour data based on the query step. It reads raw data when matching downsampled blocks are unavailable. Pass `max_source_resolution=0` to force raw data. Keeping raw data lets you zoom into a historical period at the original sampling resolution. See [Thanos Query](https://thanos.io/tip/components/query.md/) and [Compactor](https://thanos.io/tip/components/compact.md/) for details.

All components run on one node, so queries stop when that node is unavailable. COS retains uploaded data, but losing the local disk can still lose data that has not yet been uploaded.

## Object storage credentials

Create dedicated COS credentials for Thanos. Limit object read, write, and delete permissions to the metrics prefix, and grant the required bucket listing permissions. The Compactor needs to delete objects that newer blocks have replaced.

The `objstore.yml` field in the `apps/thanos-objstore` Secret holds the S3 configuration: bucket, region, endpoint, access key, secret key, and top-level `prefix: thanos/cloudlab`. Follow the [SOPS steps](secrets.md#encrypt-and-commit-secrets) to encrypt it as `platform/thanos/objstore.sops.yaml`. The Sidecar, Store Gateway, and Compactor share this Secret.

## Verification

In Grafana Explore, select **Prometheus** and query `up` for the last 15 minutes, then query a historical period already uploaded to COS. To test COS data alone, set `storeMatch[]={__address__="thanos-store.apps.svc.cluster.local:10901"}` and `partial_response=false` in the Query API request. This limits the query to the Store Gateway. Thanos endpoints are only available inside the cluster.

## Alerts

Prometheus evaluates rules and sends alerts to Alertmanager. Alertmanager groups alerts by name and sends firing and resolved notifications to ntfy. Notifications contain only the rule name and status. Watchdog and InfoInhibitor do not send ntfy notifications. There are no separate scrape targets for the k3s scheduler, controller-manager, or kube-proxy, so their three `*Down` rules are disabled.

Custom rules are defined under `additionalPrometheusRulesMap` in `platform/kube-prometheus-stack/resources.yaml`. Helm creates the corresponding `PrometheusRule`. `CloudLabDataDiskUsageHigh` fires when the ext4 filesystem at `/data` has less than 20% free space for 10 minutes. Requested local-path PVC sizes do not enforce disk quotas; applications share the available space on `/data`.

`platform/kube-prometheus-stack/alertmanager.sops.yaml` defines the `apps/cloudlab-alertmanager` Secret. Its `alertmanager.yaml` field contains native Alertmanager configuration. The ntfy webhook URL is `https://ntfy.sh`, `payload.topic` selects the topic, and the native `payload` template produces the JSON ntfy expects. After editing, encrypt and commit the file for Flux to deploy. Verify that Prometheus lists Alertmanager at `/api/v1/alertmanagers`, then check both firing and resolved notifications with a test alert.

## Thanos alert coverage

[thanos-monitoring.yaml](../platform/kube-prometheus-stack/thanos-monitoring.yaml) adds PodMonitor and PrometheusRule settings to the HelmRelease. The chart creates these resources after installing the Prometheus CRDs. The Sidecar uses the chart's ServiceMonitor; Query, Store Gateway, and Compactor use a PodMonitor. All are scraped every 30 seconds. Their job names are `thanos-sidecar`, `thanos-query`, `thanos-store`, and `thanos-compactor`.

| Alert | Condition |
| --- | --- |
| `ThanosComponentUnavailable` | A component scrape fails or its target is missing for 5 minutes |
| `ThanosSidecarPrometheusUnavailable` | The Sidecar cannot reach Prometheus for 5 minutes |
| `ThanosSidecarUploadFailed` | An upload failure occurred in the last 15 minutes, and the condition holds for 5 minutes |
| `ThanosSidecarUploadStalled` | The Sidecar has run for over 3 hours without a successful upload in the last 3 hours, and the condition holds for 15 minutes |
| `ThanosObjectStorageErrors` | COS operation errors occurred in the last 5 minutes, and the condition holds for 10 minutes |
| `ThanosCompactorHalted` | The Compactor remains halted for 5 minutes |
| `ThanosCompactorFailures` | Compaction, downsampling, or cleanup failures occurred in the last 15 minutes, and the condition holds for 5 minutes |
| `ThanosCompactorStalled` | The Compactor has run for over 1 hour without completing an iteration in the last hour, and the condition holds for 15 minutes |
| `ThanosStoreSyncFailures` | Block metadata sync or block loading failed in the last 15 minutes, and the condition holds for 5 minutes |
| `ThanosQueryStoreUnavailable` | Query has no Sidecar or Store Gateway connection for 5 minutes |

Prometheus evaluates these rules and uses the existing Alertmanager route to ntfy. Rule tests cover startup grace periods, brief errors, sustained failures, and recovery. CI runs them with `promtool test rules`. After deployment, check that all four scrape targets are UP, the `cloudlab.thanos` rule group has no evaluation errors, and a test alert produces both firing and resolved notifications.

Prometheus runs on the same node as these components. These local rules cannot send notifications when the entire node is offline.
