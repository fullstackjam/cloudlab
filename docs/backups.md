# Backup and recovery

A systemd timer on the CVM runs backups daily at 03:20 UTC. It does not use a Kubernetes CronJob. [backup.sh](../infra/backup/backup.sh) exports application data, then uses restic to encrypt and upload it to COS. restic manages snapshots, retention, and restores. Thanos manages long-term metrics separately; see [monitoring](monitoring.md).

## Backup contents

| Application | Export file | Included data | Recreate during recovery |
| --- | --- | --- | --- |
| InfluxDB | `apple-health.csv` | Annotated query CSV from the `apple-health` bucket | Users, tokens, and other configuration |
| Uptime Kuma | `kuma.db` | Monitor configuration, history, status pages, and database migration markers | Administrators, sessions, notifications, and monitor credentials |
| Health Auto Export | `health-auto-export.archive.gz` | Documents, collections, and indexes from the MongoDB application database | Database users and roles |

For Kuma, the script creates an online SQLite backup and cleans the copy. It clears user, session, notification, and other tables not on the retention list; clears monitor credential fields; and removes usernames, passwords, and query strings from URLs. Names, descriptions, tags, and URL paths remain, so do not store credentials in those fields. Check that the export script supports the new schema when upgrading Kuma.

Backups do not include raw PVCs, cluster state, kubeconfig, or Secrets. Flux restores Grafana configuration and dashboards stored in Git. Content created only in the UI is not currently backed up. Recover application credentials from the credential vault.

## Initial setup

Install restic, Python 3, kubectl, and flock on the k3s node. The script needs local access to Kuma's data files under `/data/k3s/storage`.

Create `/etc/cloudlab/backup.env`, owned by root with mode `0600`, and set these variables:

| Variable | Value |
| --- | --- |
| `RESTIC_REPOSITORY` | `s3:https://cos.na-ashburn.myqcloud.com/BUCKET-APPID/restic/cloudlab` |
| `RESTIC_PASSWORD` | The restic repository encryption password, stored separately in the credential vault |
| `AWS_ACCESS_KEY_ID` | A COS access key limited to the backup prefix |
| `AWS_SECRET_ACCESS_KEY` | The matching COS secret key |

Run the following commands on the node. Load the environment file only in a root shell, without printing its contents.

```bash
sudo -i
set -a
. /etc/cloudlab/backup.env
set +a
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
export CLOUDLAB_KUBE_CONTEXT=default
# Run once for a new repository; skip when restoring an existing repository.
restic -o s3.bucket-lookup=dns init
```

Run these installation commands from the repository root on the node. If the node has no checkout, copy the files to it first:

```bash
sudo install -d -m 0755 /opt/cloudlab-backup
sudo install -m 0755 infra/backup/backup.sh infra/backup/export-kuma.py /opt/cloudlab-backup/
sudo install -m 0644 infra/backup/systemd/cloudlab-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start cloudlab-backup.service
sudo journalctl -u cloudlab-backup.service -n 50 --no-pager
```

After the first backup succeeds and you have tested an isolated restore, enable the timer:

```bash
sudo systemctl enable --now cloudlab-backup.timer
sudo systemctl list-timers cloudlab-backup.timer
```

The script retains snapshots from the last 30 days. After each backup, it prunes unused data and checks a 5% sample. With COS versioning, deleted restic object versions may remain for another 35 days. Do not set age-based expiration for restic objects still in use. Regularly check disk space, bucket usage, and backup results.

## Restore a snapshot

Run these commands in the root shell with the credentials loaded above:

```bash
restic -o s3.bucket-lookup=dns snapshots --tag cloudlab
restic -o s3.bucket-lookup=dns check
restic -o s3.bucket-lookup=dns restore "$SNAPSHOT_ID" --target /data/restore-drill
```

Set `SNAPSHOT_ID` to the snapshot to restore. Export files are under a `data/cloudlab-backup.*` subdirectory in the restore target. Restore into an isolated namespace or node first. Do not create public HTTPRoutes or overwrite active volumes.

### InfluxDB

Deploy a separate InfluxDB 2.x instance and initialize the `cloudlab` organization and `apple-health` bucket. Supply a write token through `INFLUX_TOKEN`, then convert the CSV with [restore-influxdb.py](../infra/backup/restore-influxdb.py):

```bash
python3 infra/backup/restore-influxdb.py < "$RESTORED_CSV" > "$RESTORED_LINE_PROTOCOL"
influx write --host "$DRILL_INFLUX_URL" --bucket apple-health --org cloudlab \
  --precision ns --file "$RESTORED_LINE_PROTOCOL"
```

`RESTORED_CSV` points to the restored `apple-health.csv`, `RESTORED_LINE_PROTOCOL` is the converted file, and `DRILL_INFLUX_URL` is the isolated instance's URL. Do not pass query CSV directly to `influx write`. Check measurement counts, time ranges, field types, and sample values. See [Influx query CSV](https://docs.influxdata.com/influxdb/v2/query-data/execute-queries/influx-cli/) for the export format.

The CSV does not restore InfluxDB authorizations. Create a new token with write access to the restored `apple-health` bucket, update `influx-write-token` in `apps/apple-health-influxdb/credentials.sops.yaml`, and deploy it before accepting uploads. Keep the receiver's `auth-token` unchanged so the iPhone can keep using its existing credentials. Keep plaintext tokens out of terminal output and Git.

### Uptime Kuma

Stop Kuma in the isolated environment, place `kuma.db` in its `/app/data` volume, and allow the container user to read and write it. Then start the service. Recreate the administrator and recover monitor credentials and notification settings from the vault. Do not include WAL files from an old database. Check monitor counts, configuration, and history.

After creating the administrator, stop Kuma again and assign the restored monitors to that account. The export removes user ownership along with users; Kuma's setup does not reassign it. Set `KUMA_DATABASE` to the restored volume's `kuma.db` path and `KUMA_USERNAME` to the administrator's username:

```bash
python3 - "$KUMA_DATABASE" "$KUMA_USERNAME" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    users = db.execute('SELECT id FROM user WHERE username=?', (sys.argv[2],)).fetchall()
    if len(users) != 1:
        raise SystemExit('Create the administrator first')
    db.execute('UPDATE monitor SET user_id=? WHERE user_id IS NULL OR user_id=0', users[0])
PY
```

Restart Kuma and verify the monitor list through an authenticated session. An HTTP 200 response or a running Pod does not prove that the restored monitors are usable. If Flux manages the Deployment, suspend its reconciliation while stopping and restoring Kuma, then resume it after verification.

### MongoDB

Use the MongoDB 8.0 deployment from Git and compatible Database Tools. Its init script creates the application user from the deployment Secret. Restore through the loopback address inside the Pod; pass the password through an anonymous pipe:

```bash
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" -n "$DRILL_NAMESPACE" exec -i "$DRILL_POD" -- \
  bash -ec 'mongorestore --host 127.0.0.1 --username health-auto-export \
    --authenticationDatabase admin \
    --config=<(printf "password: %s\n" "$MONGO_APP_PASSWORD") \
    --archive --gzip --stopOnError --nsInclude="health-auto-export.*"' < "$RESTORED_ARCHIVE"
```

`RESTORED_ARCHIVE` points to the restored `health-auto-export.archive.gz`. Check mongorestore's success and failure counts, document counts for each collection, indexes, and samples of metrics, workouts, and routes. Stop Health Auto Export while restoring so it cannot write concurrently. The command expects a fresh application database; do not merge it into a populated database without deciding how to handle existing documents.

Single-node MongoDB uses an online logical export. Writes across collections during the export may be captured at different points in time. After recovery, you can upload the affected dates again from the iPhone.

Record the snapshot used, data checks, and actual recovery time outside this repository. Daily backups target an RPO of 24 hours, provided scheduled jobs succeed. Measure RTO during a recovery test.

## Replace a node

Use the [node recovery workflow](recovery.md) for the complete procedure and automated entry point.

Retrieve the age private key, Git access credentials, COS keys, restic password, OpenTofu state, and infrastructure resource IDs. Follow the [bootstrap guide](bootstrap.md) to prepare the replacement node, data disk, and k3s. Bootstrap Flux from Git, then restore application data and accounts.

Before reusing a data disk, decide whether to recover the existing cluster state or rebuild it, then handle `/data/k3s/server` accordingly. Only one k3s server may use that directory. Confirm that the old node has stopped using the disk before switching. Reuse the existing Tunnel and connector credential unless the credential needs to be revoked; stop the old connector before enabling the restored node.
