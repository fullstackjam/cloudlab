# Apple Health 接入

CloudLab 支持两条 Apple Health 导入链路，接收地址决定数据进入哪个数据库。

| 用途 | 上传路径 | 服务清单 | 存储与面板 |
| --- | --- | --- | --- |
| 官方 Health Auto Export 面板 | `/api/data` | [接收器清单](../apps/health-auto-export/resources.yaml) 和 [MongoDB 清单](../apps/health-auto-export/mongodb.yaml) | MongoDB，官方三个面板 |
| InfluxDB 数据导入 | `/api/healthautoexport/v1/influxdb/ingest` | [InfluxDB 接收器清单](../apps/apple-health-influxdb/resources.yaml) | InfluxDB 的 `cloudlab/apple-health` bucket，通过 Data Explorer 查询 |

两条 REST API 使用同一个 `health-ingest.<domain>`，分别使用各自的写入凭据。官方面板的部署和手机配置见 [Health Auto Export 官方面板](#health-auto-export-官方面板)。

InfluxDB 还支持在管理机上转换 Apple 导出的 XML。先核对 measurement、单位、时区和重复时间戳，原始 XML 和转换结果保存在受控的本地目录。

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

## iPhone 自动导入到 InfluxDB

Health Auto Export 使用 REST API 自动化发送 JSON。集群中的 `apple-health-ingester` 验证请求并把指标或训练记录写入 `apple-health` bucket。

首次部署新环境时，先启动 InfluxDB，再为接收器准备随机生成的导出 token 和仅能写入 `apple-health` 的 InfluxDB token。创建 `apps/apple-health-ingester` Secret，键名为 `auth-token` 和 `influx-write-token`，用本次部署的 age 公钥将其加密为 `apps/apple-health-influxdb/credentials.sops.yaml`。把密文加入该组件的 `kustomization.yaml`。`apps/apple-health-influxdb` 同时加载接收器清单和密文，由 apps 同步入口解密。

- URL 为 `https://health-ingest.<domain>/api/healthautoexport/v1/influxdb/ingest`。发布方式见 [DNS 与访问控制](dns.md)。
- HTTP Header 的名称为 `Authorization`，值为 `Bearer <导出 token>`。使用为接收器生成的导出 token。InfluxDB 的写入 token 由接收器持有。
- 配置导出任务的数据类型、指标和频率。填好 URL 和 header 后，先用 **Manual Upload** 发一个短时间段，并从 InfluxDB 核对是否写入。

## Health Auto Export 官方面板

Grafana 的 **Health Auto Export** 文件夹载入官方的 **Health Metrics**、**Workouts Table** 和 **Workout Details**。这三块面板通过 Infinity 查询 `health-auto-export` 服务的 HTTP API，该服务将收到的数据存入集群内 MongoDB。InfluxDB 导入链路的数据独立保存，可通过 Data Explorer 查询。服务代码和面板在部署时从固定的上游 Git 提交获取。[官方配置说明](https://github.com/HealthyApps/health-auto-export-server#setup-guide)

首次部署先准备以下两个 SOPS Secret。接收器凭据放在 `apps/health-auto-export`，Grafana 数据源凭据放在 `platform/kube-prometheus-stack`，分别加入目录内的 `kustomization.yaml`。两者分别随 apps 和 platform 同步入口解密。

- `apps/health-auto-export` 包含独立的 `write-token`、`read-token`、`mongo-root-password` 和 `mongo-app-password`。两个 API token 都用 `sk-` 加随机字符串。MongoDB 密码用随机十六进制字符串以便上游构造连接 URL。首次启动时，MongoDB 为应用用户授予该数据库的读写权限。
- `apps/grafana-health-auto-export-datasource` 带 `grafana_datasource: "1"` 标签，并以 `health-auto-export.yaml` 为键提供下列数据源。 `<read-token>` 与服务使用的读取 token 一致。

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

iPhone 分别创建 **Health Metrics** 和 **Workouts** 自动化，各自指向 `https://health-ingest.<domain>/api/data`。两条都使用 JSON，并设置 `api-key` 为写入 token。先用 **Manual Upload** 上传一个短时间段，再打开对应面板核对。两条导入链路可以同时使用。

公网入口只转发 `POST /api/data`。Grafana 使用读取 token，在集群内查询官方服务。MongoDB 使用单节点持久卷，应用数据库纳入主机定时备份，与 InfluxDB 和 Kuma 数据一起加密上传到 COS。恢复步骤见 [备份指南](backups.md)。
