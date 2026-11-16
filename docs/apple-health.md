# Apple Health

CloudLab supports two Apple Health import paths. The upload endpoint determines which database receives the data.

| Purpose | Upload path | Service manifests | Storage and dashboards |
| --- | --- | --- | --- |
| Official Health Auto Export dashboards | `/api/data` | [Receiver](../apps/health-auto-export/resources.yaml) and [MongoDB](../apps/health-auto-export/mongodb.yaml) | MongoDB and three official dashboards |
| InfluxDB import | `/api/healthautoexport/v1/influxdb/ingest` | [InfluxDB receiver](../apps/apple-health-influxdb/resources.yaml) | The `cloudlab/apple-health` bucket in InfluxDB, queried through Data Explorer |

Both REST APIs use `health-ingest.<domain>`, with separate write credentials. See [official Health Auto Export dashboards](#official-health-auto-export-dashboards) for server and phone setup.

You can also convert an Apple XML export on your management machine for import into InfluxDB. Check measurements, units, time zones, and duplicate timestamps first. Keep the original XML and converted files in a local directory with restricted access.

## Import from a management machine

Prepare the [kubeconfig and SSH forwarding](bootstrap.md#4-prepare-kubeconfig-and-encrypted-secrets) from the bootstrap guide. Keep SSH forwarding running and set `CLOUDLAB_KUBE_CONTEXT` to the context name in your kubeconfig. In another terminal, forward InfluxDB to the local loopback address.

```bash
kubectl --kubeconfig .local/kubeconfig-cloudlab --context "$CLOUDLAB_KUBE_CONTEXT" -n apps \
  port-forward --address 127.0.0.1 svc/influxdb 18086:8086
```

Open `http://127.0.0.1:18086`. In InfluxDB, create an API token with write access only to the `apple-health` bucket and save it in the credential vault. In another terminal, supply it through the `INFLUX_TOKEN` environment variable, then import the file.

```bash
curl -fsS http://127.0.0.1:18086/health
influx write --host http://127.0.0.1:18086 --org cloudlab --bucket apple-health \
  --precision ns --file "$CLOUDLAB_HEALTH_LINE_PROTOCOL"
```

`CLOUDLAB_HEALTH_LINE_PROTOCOL` points to a line protocol file with nanosecond timestamps. Import one measurement first and check timestamps, fields, and values in InfluxDB's Data Explorer before importing the rest.

## Automatic iPhone imports into InfluxDB

Health Auto Export sends JSON through a REST API automation. The cluster's `apple-health-ingester` validates each request and writes metrics or workout records to the `apple-health` bucket.

For a new deployment, start InfluxDB first. Then generate a random receiver token and an InfluxDB token that can only write to `apple-health`. Create the `apps/apple-health-ingester` Secret with keys `auth-token` and `influx-write-token`. Encrypt it with this deployment's age public key as `apps/apple-health-influxdb/credentials.sops.yaml`, and add it to the component's `kustomization.yaml`. `apps/apple-health-influxdb` loads both the receiver manifests and encrypted credentials; the apps sync entry decrypts them.

- Set the URL to `https://health-ingest.<domain>/api/healthautoexport/v1/influxdb/ingest`. See [DNS and access control](dns.md) for publishing the endpoint.
- Set the HTTP header name to `Authorization` and its value to `Bearer <receiver-token>`. Use the token generated for the receiver. The receiver holds the InfluxDB write token.
- Choose the data type, metrics, and export frequency. After setting the URL and header, use **Manual Upload** for a short time range and check the imported data in InfluxDB.

## Official Health Auto Export dashboards

Grafana's **Health Auto Export** folder contains the official **Health Metrics**, **Workouts Table**, and **Workout Details** dashboards. They use Infinity to query the `health-auto-export` HTTP API, which stores incoming data in MongoDB inside the cluster. Data sent through the InfluxDB import path is stored separately and can be queried through Data Explorer. The service code and dashboards are fetched from a pinned upstream Git commit during deployment. See the [official setup guide](https://github.com/HealthyApps/health-auto-export-server#setup-guide).

Prepare the following two SOPS Secrets before the first deployment. Put receiver credentials in `apps/health-auto-export` and Grafana data source credentials in `platform/kube-prometheus-stack`. Add each file to its directory's `kustomization.yaml`. The apps and platform sync entries decrypt their respective Secrets.

- `apps/health-auto-export` contains separate `write-token`, `read-token`, `mongo-root-password`, and `mongo-app-password` values. Each API token uses `sk-` followed by a random string. Use random hexadecimal strings for MongoDB passwords so the upstream service can build connection URLs. On first startup, MongoDB grants the application user read/write access to its database.
- `apps/grafana-health-auto-export-datasource` has the label `grafana_datasource: "1"` and provides the following data source under the `health-auto-export.yaml` key. `<read-token>` must match the service's read token.

```yaml
apiVersion: 1
datasources:
  - name: Health Auto Export
    uid: bee4k1otx5urkf
    type: yesoreyeram-infinity-datasource
    access: proxy
    url: http://health-auto-export.apps.svc.cluster.local:3001
    editable: false
    jsonData:
      auth_method: none
      httpHeaderName1: api-key
      allowedHosts:
        - http://health-auto-export.apps.svc.cluster.local:3001
    secureJsonData:
      httpHeaderValue1: <read-token>
```

On the iPhone, create separate **Health Metrics** and **Workouts** automations, both pointing to `https://health-ingest.<domain>/api/data`. Use JSON for both and set `api-key` to the write token. Start with **Manual Upload** for a short time range, then check the corresponding dashboard. Both import paths can be used at the same time.

The public endpoint forwards only `POST /api/data`. Grafana uses the read token to query the official service inside the cluster. MongoDB uses a single-node persistent volume. Its application database is included in the scheduled host backup, encrypted, and uploaded to COS alongside InfluxDB and Kuma data. See the [backup guide](backups.md) for recovery steps.
