# 备份与恢复

备份由 CVM 上的 systemd timer 每天在 03:20 UTC 运行，不使用 Kubernetes CronJob。[backup.sh](../infra/backup/backup.sh) 导出应用数据，再用 restic 加密上传到 COS。restic 管理快照、保留期和恢复；Thanos 的长期指标存储独立管理，见 [监控文档](monitoring.md)。

## 备份范围

| 应用 | 导出文件 | 包含内容 | 恢复时需重建 |
| --- | --- | --- | --- |
| InfluxDB | `apple-health.csv` | `apple-health` bucket 的 annotated query CSV | 用户、token 和其他配置 |
| Uptime Kuma | `kuma.db` | 监控配置、历史、状态页和数据库迁移标记 | 管理员、会话、通知及监控凭据 |
| Health Auto Export | `health-auto-export.archive.gz` | MongoDB 应用数据库的文档、集合和索引 | 数据库用户与角色 |

Kuma 使用 SQLite 在线备份后清理副本：清空用户、会话、通知及未列入保留清单的表，清除监控凭据字段，并移除 URL 中的用户名、密码和 query。名称、描述、标签和 URL 路径仍会保留，不应在这些字段中存放凭据。升级 Kuma 时需检查导出脚本是否适配新 schema。

备份不包含原始 PVC、集群状态、kubeconfig 或 Secret。Grafana 的仓库配置和面板由 Flux 恢复；只在 UI 中创建的内容不在当前备份范围内。应用凭据从凭据库恢复。

## 首次配置

在 k3s 节点准备 restic、Python 3、kubectl 和 flock。脚本需要本机访问 `/data/k3s/storage` 下的 Kuma 数据文件。

创建 root 所有、权限为 `0600` 的 `/etc/cloudlab/backup.env`，填写以下变量：

| 变量 | 内容 |
| --- | --- |
| `RESTIC_REPOSITORY` | `s3:https://cos.na-ashburn.myqcloud.com/BUCKET-APPID/restic/cloudlab` |
| `RESTIC_PASSWORD` | restic 仓库加密密码，独立保存到凭据库 |
| `AWS_ACCESS_KEY_ID` | 限定到备份前缀的 COS access key |
| `AWS_SECRET_ACCESS_KEY` | 对应的 COS secret key |

以下命令在节点上执行。环境文件只在 root shell 中加载，不输出内容。

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

在节点上的仓库根目录执行以下安装命令；若节点没有仓库副本，先将这些文件复制到节点：

```bash
sudo install -d -m 0755 /opt/cloudlab-backup
sudo install -m 0755 infra/backup/backup.sh infra/backup/export-kuma.py /opt/cloudlab-backup/
sudo install -m 0644 infra/backup/systemd/cloudlab-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start cloudlab-backup.service
sudo journalctl -u cloudlab-backup.service -n 50 --no-pager
```

确认首次备份成功并完成一次隔离恢复后，开启定时任务：

```bash
sudo systemctl enable --now cloudlab-backup.timer
sudo systemctl list-timers cloudlab-backup.timer
```

脚本保留最近 30 天内的快照，并在每次备份后执行 prune 和 5% 数据抽查。COS 版本控制下，被删除的 restic 对象版本还可能保留 35 天；不要对仍在使用的 restic 对象设置按龄过期。定期检查磁盘、桶容量和备份执行结果。

## 恢复快照

在加载了上述凭据的 root shell 中运行：

```bash
restic -o s3.bucket-lookup=dns snapshots --tag cloudlab
restic -o s3.bucket-lookup=dns check
restic -o s3.bucket-lookup=dns restore "$SNAPSHOT_ID" --target /data/restore-drill
```

`SNAPSHOT_ID` 填写要恢复的快照 ID。导出文件位于恢复目录中的 `data/cloudlab-backup.*` 子目录。先恢复到隔离命名空间或节点，不创建公网 HTTPRoute，不覆盖运行中的卷。

### InfluxDB

部署独立的 InfluxDB 2.x 实例，初始化 `cloudlab` org 和 `apple-health` bucket。将写入 token 注入 `INFLUX_TOKEN`，用 [restore-influxdb.py](../infra/backup/restore-influxdb.py) 转换 CSV：

```bash
python3 infra/backup/restore-influxdb.py < "$RESTORED_CSV" > "$RESTORED_LINE_PROTOCOL"
influx write --host "$DRILL_INFLUX_URL" --bucket apple-health --org cloudlab \
  --precision ns --file "$RESTORED_LINE_PROTOCOL"
```

`RESTORED_CSV` 指向恢复的 `apple-health.csv`，`RESTORED_LINE_PROTOCOL` 是转换后的文件，`DRILL_INFLUX_URL` 是隔离实例地址。不要直接将 query CSV 交给 `influx write`。核对 measurement 数量、时间范围、字段类型和样本值。导出格式见 [Influx query CSV](https://docs.influxdata.com/influxdb/v2/query-data/execute-queries/influx-cli/)。

### Uptime Kuma

停止隔离环境中的 Kuma，将 `kuma.db` 放入它的 `/app/data` 卷，设置为容器用户可读写，再启动服务。重新创建管理员，从凭据库恢复监控凭据和通知设置。不要混入旧数据库的 WAL 文件。核对监控数量、配置和历史记录。

### MongoDB

使用隔离的 MongoDB 8.0 实例和兼容的 Database Tools，在 Pod 内通过回环地址恢复。下面命令假设隔离实例尚未启用认证：

```bash
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" -n "$DRILL_NAMESPACE" exec -i "$DRILL_POD" -- \
  mongorestore --host 127.0.0.1 --archive --gzip --stopOnError \
  --nsInclude='health-auto-export.*' < "$RESTORED_ARCHIVE"
```

`RESTORED_ARCHIVE` 指向恢复的 `health-auto-export.archive.gz`。核对 mongorestore 的成功/失败数量、各集合文档数、索引，以及指标、训练和路线样本。部署 Secret 用于重建认证信息。

单节点 MongoDB 使用在线逻辑导出，导出期间发生的跨集合写入可能落在不同时间点。恢复后可从 iPhone 重传对应日期的数据。

记录所用快照、数据核对结果和实际恢复耗时，记录保存在仓库外。每日备份的目标 RPO 为 24 小时，前提是定时任务成功；RTO 以恢复演练实测为准。

## 更换节点

取回 age 私钥、Git 访问凭据、COS key、restic 密码、OpenTofu state 和基础资源 ID。按 [引导手册](bootstrap.md) 准备替代节点、数据盘和 k3s，从 Git 引导 Flux，再恢复应用数据和账号。

复用原数据盘时，先决定恢复原集群状态还是重建，再处理 `/data/k3s/server`；同一目录只允许一台 k3s server 使用。切换前确认旧节点已停止使用该盘。重建 Tunnel 连接器时轮换 connector token，并核对 OpenTofu state 中的资源记录。
