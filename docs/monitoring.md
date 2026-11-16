# 监控与长期存储

[kube-prometheus-stack](../platform/kube-prometheus-stack/resources.yaml) 部署 Prometheus、Alertmanager、Grafana 和采集组件。[Thanos](../platform/thanos/resources.yaml) 提供指标长期存储与统一查询。

## 指标存储与查询

Prometheus 本地保留期为 3 天，同时设置 8 GB 的容量限制，先达到的限制决定清理时间。Sidecar 将完成的 TSDB 数据块上传到 COS 的 `thanos/cloudlab/` 前缀。原始、5 分钟和 1 小时分辨率的保留期均为 `0s`，Thanos 不按时间删除这些数据；COS 生命周期也不能对当前指标块设置按龄过期。

Grafana 只保留一个 Prometheus 类型的数据源，名称为 **Prometheus**、UID 为 `prometheus`，地址指向 Thanos Query。Query 通过 Sidecar 查询 Prometheus，通过 Store Gateway 查询 COS，并返回统一结果。历史数据不会重新写入 Prometheus。官方 Health Auto Export 面板通过 HTTP API 查询 MongoDB，不经过 Thanos。

Compactor 负责合并数据块和生成降采样数据。Query 启用 `--query.auto-downsampling`，按查询步长选择原始、5 分钟或 1 小时分辨率的数据；没有对应降采样块时仍读取原始数据。需要强制使用原始数据时，传入 `max_source_resolution=0`。保留原始数据后，可以放大历史时间段查看原采样精度的指标。机制与参数见 [Thanos Query](https://thanos.io/tip/components/query.md/) 和 [Compactor](https://thanos.io/tip/components/compact.md/)。

所有组件部署在同一个节点上，节点不可用时查询也会中断。COS 保存已上传的数据，本地磁盘丢失仍可能导致尚未上传的数据丢失。

## 对象存储凭据

为 Thanos 准备专用 COS 凭据，将对象读写、删除权限限定到指标前缀，并提供所需的桶列表权限。Compactor 需要删除已被新块替代的对象。

`apps/thanos-objstore` Secret 的 `objstore.yml` 字段保存 S3 配置，包括桶名、区域、端点、access key、secret key 和顶层 `prefix: thanos/cloudlab`。按 [SOPS 步骤](secrets.md#生成并提交密文) 加密为 `platform/thanos/objstore.sops.yaml`。Sidecar、Store Gateway 和 Compactor 共用这个 Secret。

## 验证

在 Grafana Explore 选择 **Prometheus**，查询近 15 分钟的 `up`，再查询已上传到 COS 的历史时段。要单独验证 COS 中的数据，在 Query API 请求中设置 `storeMatch[]={__address__="thanos-store.apps.svc.cluster.local:10901"}` 和 `partial_response=false`，限定只查询 Store Gateway。Thanos 服务只提供集群内地址。

## 告警

Prometheus 评估规则，再将告警发送给 Alertmanager。Alertmanager 按告警名分组，向 ntfy 发送触发和恢复通知，通知只包含规则名和状态。Watchdog、InfoInhibitor 不向 ntfy 发送。当前没有为 k3s 的 scheduler、controller-manager、kube-proxy 配置独立抓取目标，对应的三条 `*Down` 规则已停用。

自定义规则放在 `platform/kube-prometheus-stack/resources.yaml` 的 `additionalPrometheusRulesMap` 下，由 Helm 创建 `PrometheusRule`。`CloudLabDataDiskUsageHigh` 在 `/data` 的 ext4 文件系统可用空间低于 20% 且持续 10 分钟时触发。local-path PVC 的申请容量不构成磁盘配额，各应用共享 `/data` 的实际空间。

`platform/kube-prometheus-stack/alertmanager.sops.yaml` 定义 `apps/cloudlab-alertmanager` Secret，`alertmanager.yaml` 字段保存原生配置。ntfy webhook 的 URL 为 `https://ntfy.sh`，`payload.topic` 指定主题，原生 `payload` 模板生成 ntfy 所需的 JSON。修改后重新加密并提交，由 Flux 部署。验证 Prometheus 的 `/api/v1/alertmanagers` 列出了 Alertmanager，再验证测试告警的触发和恢复通知。

## Thanos 告警覆盖

[thanos-monitoring.yaml](../platform/kube-prometheus-stack/thanos-monitoring.yaml) 为 HelmRelease 补充 PodMonitor 和 PrometheusRule 配置，由 chart 在安装 Prometheus CRD 后创建。Sidecar 使用 chart 的 ServiceMonitor；Query、Store Gateway 和 Compactor 使用 PodMonitor，均每 30 秒采集一次。四个 job 分别为 `thanos-sidecar`、`thanos-query`、`thanos-store` 和 `thanos-compactor`。

| 告警 | 触发条件 |
| --- | --- |
| `ThanosComponentUnavailable` | 组件采集失败或目标消失，持续 5 分钟 |
| `ThanosSidecarPrometheusUnavailable` | Sidecar 无法访问 Prometheus，持续 5 分钟 |
| `ThanosSidecarUploadFailed` | 近 15 分钟出现上传失败，条件持续 5 分钟 |
| `ThanosSidecarUploadStalled` | Sidecar 已运行超过 3 小时，且超过 3 小时没有成功上传，持续 15 分钟 |
| `ThanosObjectStorageErrors` | 最近 5 分钟内发生 COS 操作错误，条件持续 10 分钟 |
| `ThanosCompactorHalted` | Compactor 进入 halted 状态，持续 5 分钟 |
| `ThanosCompactorFailures` | 近 15 分钟出现压缩、降采样或清理失败，条件持续 5 分钟 |
| `ThanosCompactorStalled` | Compactor 已运行超过 1 小时，最近 1 小时没有完成迭代，持续 15 分钟 |
| `ThanosStoreSyncFailures` | 近 15 分钟出现块元数据同步或加载失败，条件持续 5 分钟 |
| `ThanosQueryStoreUnavailable` | Query 缺少 Sidecar 或 Store Gateway 连接，持续 5 分钟 |

这些规则由 Prometheus 执行，沿用 Alertmanager 的 ntfy 路由。规则测试覆盖启动宽限期、短暂错误、持续故障和恢复，CI 用 `promtool test rules` 执行。部署后检查四个采集目标为 UP、规则组 `cloudlab.thanos` 无评估错误，并验证一次测试告警的触发与恢复。

Prometheus 与这些组件在同一节点，节点整体离线时无法依靠本机规则发出通知。
