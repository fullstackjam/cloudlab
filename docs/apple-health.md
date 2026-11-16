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

Health Auto Export 使用 REST API 自动化发送 JSON。集群中的 `apple-health-ingester` 验证请求并把指标或训练记录写入 `apple-health` bucket。

首次部署新环境时，先启动 InfluxDB，再为接收器准备随机生成的导出 token 和仅能写入 `apple-health` 的 InfluxDB token。创建 `apps/apple-health-ingester` Secret，键名为 `auth-token` 和 `influx-write-token`，用本次部署的 age 公钥将其加密为 `secrets/apple-health-ingester.sops.yaml`。把密文和接收器清单分别加入 `secrets/` 和 `apps/` 的 Kustomize 入口。

- URL 为 `https://health-ingest.<domain>/api/healthautoexport/v1/influxdb/ingest`。发布方式见 [DNS 与访问控制](dns.md)。
- HTTP Header 的名称为 `Authorization`，值为 `Bearer <导出 token>`。使用为接收器生成的导出 token。InfluxDB 的写入 token 由接收器持有。
- 保留原有导出任务的数据类型、指标和频率。改完 URL 和 header 后，先用 **Manual Upload** 发一个短时间段，并从 InfluxDB 核对是否写入。

在 Grafana 的 **Dashboards** 中打开 **Apple Health**。面板包含活动趋势、训练心率热力图、按类型统计的每周训练时长、训练明细和耳机音量。每日数据按 Asia/Shanghai 划分，训练统计使用面板选定的时间范围。

面板改编自 Marrow Health 的 Body Ops，沿用其概览卡片、趋势图和热力图，并改用 CloudLab 的 `apple-health` bucket 和字段。当前入库的心率指标来自训练和训练后恢复样本。上游代码采用 MIT 许可，版权和许可文本保存在 [apple-health-MIT.txt](../platform/infra/dashboards/apple-health-MIT.txt)。 [Body Ops 源文件](https://github.com/MarrowHealth/built-with-marrow/blob/7d6814aad3ccd362ce36635de03046ea23a9d567/projects/body-ops/grafana/dashboards/body-ops.json)

新环境先为 Grafana 创建一个只有 `apple-health` 读取权限的 InfluxDB token。在忽略的 `.local/` 目录下按下面的模板准备 Secret，将 token 填进 `secureJsonData.token`。

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: grafana-apple-health-datasource
  namespace: apps
  labels:
    grafana_datasource: "1"
type: Opaque
stringData:
  apple-health-datasource.yaml: |
    apiVersion: 1
    datasources:
      - name: Apple Health
        uid: apple-health-influxdb
        type: influxdb
        access: proxy
        url: http://influxdb.apps.svc.cluster.local:8086
        editable: false
        jsonData:
          version: Flux
          organization: cloudlab
          defaultBucket: apple-health
        secureJsonData:
          token: <bucket-read-token>
```

将模板保存为 `.local/grafana-apple-health-datasource.yaml`，用 [.sops.yaml](../.sops.yaml) 中本次部署的 age 公钥加密。解密校验通过后，删除本地明文。

```bash
sops encrypt --filename-override secrets/grafana-apple-health-datasource.sops.yaml \
  .local/grafana-apple-health-datasource.yaml > .local/grafana-apple-health-datasource.sops.yaml &&
sops decrypt .local/grafana-apple-health-datasource.sops.yaml > /dev/null &&
mv .local/grafana-apple-health-datasource.sops.yaml secrets/grafana-apple-health-datasource.sops.yaml
```

将密文加入 `secrets/kustomization.yaml`。Grafana 会加载数据源，并读取由 `platform/infra/kustomization.yaml` 生成的 Apple Health 面板 ConfigMap。
