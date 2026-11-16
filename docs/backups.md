# Backup and restore drill
Run scripts only after k3s is deliberately installed and tools available. Configure `RESTIC_REPOSITORY=s3:https://cos.na-ashburn.myqcloud.com/BUCKET-APPID/restic/cloudlab`, RESTIC_PASSWORD, AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY from the new 1Password items. Never put the env file in Git. `restic init` is a one-time operator action against the future bucket. Run `sudo --preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD,AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY,KUBECONFIG ./scripts/backup.sh` with the k3s kubeconfig path supplied. Requires restic, python3, kubectl, flock and node-local access.

After a successful drill, install the [systemd service](../ops/systemd/cloudlab-backup.service) and [timer](../ops/systemd/cloudlab-backup.timer) in `/etc/systemd/system`. Copy `scripts/backup.sh` and `scripts/export-data.py` to `/opt/cloudlab-backup/scripts` and install restic. Use a root-owned mode-0600 `/etc/cloudlab/backup.env` for `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Use credentials scoped to `restic/cloudlab/`. Run `systemctl daemon-reload`, run `systemctl start cloudlab-backup.service` once, then enable `cloudlab-backup.timer` for 03:20 UTC daily. Restic retains snapshots within 30 days and prunes; versioned deleted COS packs can consume space for another 35 days. No age-based expiration of live restic objects. Check disk and COS growth weekly; check restore monthly. Single node means downtime during disk/node failure, not HA.

The script exports the `apple-health` bucket as annotated query CSV, creates a sanitized online SQLite backup of Kuma, and exports the `health-auto-export` MongoDB database as `health-auto-export.archive.gz`. The MongoDB archive includes BSON documents and collection/index metadata. It excludes users, roles and the admin/config/local databases. The dump runs with the application database credential passed through an anonymous pipe. This is intentionally data-only: Influx users/tokens are re-created from 1Password. Kuma user/session/notification/unknown tables are cleared; monitor credential fields are blanked and URL userinfo/query removed. The backup retains Kuma's database migration markers, including the Knex migration tables used by Kuma 2. Review new Kuma schema and ensure URLs/descriptions/tags contain no embedded secrets before each upgrade. Sanitization may remove integrations; reconfigure from 1Password. No raw PV copies, Git, container images, cluster state, secrets, or kubeconfigs go to COS. Grafana configuration/dashboards should be provisioned from Git; later UI-only data must receive an explicit sanitized exporter, never blanket disk backup.

Drill on an isolated namespace/node without public HTTPRoutes:

1. `restic snapshots --tag cloudlab`, `restic check`, then `restic restore SNAPSHOT --target /data/restore-drill`; inspect the restored nested staging directory. Never restore over live PVs.
2. Deploy a separate InfluxDB 2.x instance and initialize new auth from 1Password. Convert the query CSV to line protocol with an audited CSV parser: group annotated CSV blocks, preserve measurement, tags, field type and nanosecond `_time`. Do not feed query CSV directly to `influx write` (its annotations are query-specific). Then `influx write --bucket apple-health --org cloudlab --precision ns --file health.lp`. Compare measurement counts, timestamps and sample values. A reusable importer is supplied as `scripts/restore-health.py`.
3. Stop the drill Kuma deployment, copy sanitized `kuma.db` into its fresh `/app/data` PVC, set ownership to its image UID, start; complete fresh admin setup and re-enter monitor credentials/notifications from 1Password. Compare monitor count and history. Never merge WAL from a previous database.
4. Record snapshot ID, exported row counts, app checks and elapsed recovery time in a dated drill note. Delete the isolated drill only after success. Target RPO 24h; RTO must be measured, not assumed.

Influx query export: https://docs.influxdata.com/influxdb/v2/query-data/execute-queries/influx-cli/ . Native backup/restore is documented at https://docs.influxdata.com/influxdb/v2/admin/backup-restore/ but is excluded here because full metadata includes auth state.


## MongoDB 恢复

每日备份包含官方 Health Auto Export 的指标、训练记录和路线数据，保留期为 30 天。归档经过 gzip 压缩和 restic 加密后上传到 COS。单节点 MongoDB 使用在线逻辑导出，上传期间发生的跨集合写入可能落在不同时间点。恢复后可从 iPhone 重传对应日期以补齐数据。

从 COS 恢复指定快照，找到 `health-auto-export.archive.gz`，先在隔离的 MongoDB 8.0 实例中恢复。使用与备份兼容的 Database Tools 版本。隔离实例通过回环地址运行，且不创建 Service 或 HTTPRoute。

```bash
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" -n "$DRILL_NAMESPACE" exec -i "$DRILL_POD" -- \
  mongorestore --host 127.0.0.1 --archive --gzip --stopOnError \
  --nsInclude='health-auto-export.*' < "$RESTORED_ARCHIVE"
```

检查 mongorestore 的成功/失败数量，再核对各集合的文档数、索引以及指标、训练和路线的样本。认证信息由部署 Secret 重建。旧快照可能只有 InfluxDB 和 Kuma 文件，恢复前检查快照内容。只有新快照包含 MongoDB 归档。
