# Apple Health 接入

CloudLab 把 Apple Health 数据写入 InfluxDB 2.x 的 `cloudlab` 组织和 `apple-health` bucket。数据由 iPhone 上的导出应用读取，或者由你在管理机上转换 Apple 导出的 XML。先核对转换后的 measurement、单位、时区和重复时间戳，原始 XML 和转换结果保存在受控的本地目录。

## 从管理机导入

准备 [bootstrap 中的 kubeconfig 和 SSH 转发](bootstrap.md#4-准备-kubeconfig-和-sops-密文)。保持 SSH 转发运行，在 `CLOUDLAB_KUBE_CONTEXT` 中填写 kubeconfig 的上下文名，再开一个终端把 InfluxDB 转发到本机回环地址。

```bash
kubectl --kubeconfig .local/kubeconfig-cloudlab --context "$CLOUDLAB_KUBE_CONTEXT" -n apps \
  port-forward --address 127.0.0.1 svc/influxdb 18086:8086
```

打开 `http://127.0.0.1:18086`，在 InfluxDB 中为 `apple-health` 创建一个只有该 bucket 写权限的 API token，存入凭据库。在另一个终端把它注入 `INFLUX_TOKEN` 环境变量，再导入文件。

```bash
curl -fsS http://127.0.0.1:18086/health
influx write --host http://127.0.0.1:18086 --org cloudlab --bucket apple-health \
  --precision ns --file "$CLOUDLAB_HEALTH_LINE_PROTOCOL"
```

`CLOUDLAB_HEALTH_LINE_PROTOCOL` 指向以纳秒为时间戳单位的 line protocol 文件。先导入一个 measurement，用 InfluxDB UI 的 Data Explorer 核对时间、字段和数值，再批量导入。

## iPhone 自动导入与 Grafana

从 iPhone 自动发送时，选支持 InfluxDB v2 line protocol 的导出应用，并为手机提供能访问写接口的私有网络，或按 [DNS 与访问控制](dns.md) 发布受保护的 `https://health.<domain>`。在导出应用中配置上述组织、bucket 和仅写入该 bucket 的 token。

在 Grafana 中查看 Apple Health 时，单独为数据源创建一个只有 `apple-health` 读取权限的 token。若通过 GitOps 配置数据源，用 SOPS 管理该 token。使用 `http://influxdb.apps.svc.cluster.local:8086` 作为 Grafana 的 InfluxDB 地址。
