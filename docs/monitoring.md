# 监控与长期存储

Prometheus 继续抓取并在本地保留 3 天或 8 GB。Thanos Sidecar 把已封口的 TSDB 块上传到 COS 的 `thanos/cloudlab/`，其中包括接入时仍保留的已压缩块。Thanos Store Gateway 从 COS 读历史数据。Thanos Query 统一查询 Sidecar 和 Store Gateway，Grafana 原有的 **Prometheus** 数据源走这个入口，名称和 UID 沿用原值。

## 凭据与保留

为 Thanos 准备一套专用 COS 凭据，读写和删除对象只授予指标前缀。Compactor 会删除已被新块替代的对象。原始指标、5 分钟和 1 小时分辨率的保留期均为 `0s`，即无时间上限。COS 生命周期对当前指标块也保持无按龄过期。

在 `apps` 命名空间创建名为 `thanos-objstore` 的 Secret，用 `objstore.yml` 保存 S3 配置。内容包含现有桶、区域、S3 端点、专用 access key、secret key 和顶层 `prefix: thanos/cloudlab`。用本次部署的 age 公钥加密后提交到 `secrets/thanos-objstore.sops.yaml`。

## 验证

在 Grafana Explore 选 **Prometheus**，用 `up` 查询近 15 分钟。再在接入前本地保留的时段查询同一个指标。独立验收 COS 时，用一个只连接 Store Gateway 的临时 Querier 查询该历史时间段，避免命中 Sidecar 的本地副本。Thanos 服务均使用集群内地址。

## 告警

Prometheus 评估规则，并把告警交给集群内的 Alertmanager。Alertmanager 按告警名分组，向配置的 ntfy 主题发送触发和恢复通知。Webhook 只发送规则名和状态。Watchdog、InfoInhibitor 留在内部路由。参考部署没有为 k3s 自带的 scheduler、controller-manager、kube-proxy 配置独立抓取目标，因此停用对应的三条 `*Down` 规则。

新环境用自己的 age 公钥重新加密 `secrets/cloudlab-alertmanager.sops.yaml`。它是 `apps/cloudlab-alertmanager` Secret，`alertmanager.yaml` 保存原生配置，其中 ntfy webhook 的 URL 为 `https://ntfy.sh`，`payload.topic` 为目标主题。部署后检查 Prometheus 的 `/api/v1/alertmanagers` 列出了 Alertmanager，再验证一条测试通知。
