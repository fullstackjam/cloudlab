# CloudLab

在腾讯云 CVM 上运行的单节点 k3s 环境，通过 Flux 从 Git 同步 Kubernetes 配置，使用 SOPS/age 管理加密凭据。Cloudflare Tunnel 提供 HTTPS 入口，COS 保存应用备份和 Prometheus 长期指标。

## 架构

```mermaid
flowchart LR
    Git[Git 仓库] --> Flux
    Flux --> Platform[入口与监控组件]
    Flux --> Apps[应用服务]
    Tunnel[Cloudflare Tunnel] --> Traefik
    Traefik --> Apps
    iPhone --> Ingest[Health Auto Export 接收器]
    Ingest --> MongoDB
    iPhone --> IngestInflux[Apple Health InfluxDB 接收器]
    IngestInflux --> InfluxDB
    MongoDB --> API[官方查询 API]
    API --> Grafana
    Prometheus -->|Sidecar 上传数据块| COS
    Grafana -->|指标查询| Query[Thanos Query]
    Query -->|Sidecar| Prometheus
    Query --> Store[Thanos Store Gateway]
    Store --> COS
    Backup[主机定时备份] --> COS
```

官方 Health Auto Export 面板通过 HTTP API 查询 MongoDB。InfluxDB 提供独立的数据导入与查询。两条导入链路和各自的凭据见 [Apple Health 接入](docs/apple-health.md)。Prometheus 的本地留存、Thanos 查询和长期保存配置见 [监控手册](docs/monitoring.md)，应用数据的备份与恢复见 [备份手册](docs/backups.md)。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| [infra/](infra/) | 云资源与集群引导。根下是 OpenTofu，`bootstrap.sh` 安装 Flux；`flux/` 声明同步入口、依赖和共享设置；`backup/` 放主机备份、恢复脚本及 systemd 定时任务 |
| [platform/](platform/) | Traefik、cloudflared、kube-prometheus-stack 和 Thanos；Gateway API CRD 位于 `traefik/crds/` |
| [apps/](apps/) | 应用服务。每个服务独立一个目录，包含工作负载、Service、PVC、配置、HTTPRoute 和所需密文；Health Auto Export 的 MongoDB 放在该应用目录内 |
| [scripts/](scripts/) | 仓库校验工具与 `tests/` 回归用例 |
| [docs/](docs/) | 部署、凭据、DNS、监控、Apple Health 和恢复操作指南 |

每个组件的 `kustomization.yaml` 列出部署资源。`source.yaml` 定义 Helm 仓库，`resources.yaml` 保存工作负载或 HelmRelease，`*.sops.yaml` 和 `*.example.yaml` 分别保存加密凭据和填写示例，直接放在组件目录中。

Flux 按 `gateway-crds → platform → apps` 同步。Gateway API CRD 必须先于 Traefik 和 HTTPRoute 创建；platform 就绪后部署应用。platform 和 apps 各自解密 Secret，并从共享设置读取域名。`kube-prometheus-stack` 部署 Prometheus、Alertmanager、Grafana 和采集组件，也管理 Grafana 的路由、面板和数据源。

`infra/flux/settings.yaml` 中的 `CLOUDLAB_DOMAIN` 同时供 OpenTofu、应用 HTTPRoute 和 Homepage 使用。公网 DNS 记录由 [dns-config](https://github.com/fullstackjam/dns-config) 管理。

## 部署与维护

1. 从零部署按 [bootstrap 手册](docs/bootstrap.md) 准备主机、云资源、凭据和 Git 同步。
2. 凭据生成、加密和恢复按 [凭据手册](docs/secrets.md)，入口域名与路由按 [DNS 手册](docs/dns.md)。
3. 修改应用或平台配置后运行验证，提交到 Flux 读取的分支，核对同步版本、工作负载和实际服务。
4. 更换节点或数据盘时，按 [备份手册](docs/backups.md) 在隔离环境验证恢复，再切换流量。

参考环境为 Ubuntu 24.04、单节点 k3s，数据盘挂载在 `/data`，本地卷位于 `/data/k3s/storage`。local-path 的 PVC 申请容量不构成磁盘配额，磁盘容量与告警配置见监控手册。节点升级可能造成服务中断，需核对版本兼容性和恢复能力。删除或移动 PVC 前检查 Flux prune 和卷回收策略。

## 验证

```bash
./scripts/validate.sh
```

[Validate CI](.github/workflows/validate.yml) 在 PR 和 `main` push 时运行凭据扫描及同一验证入口。该入口检查 shell/YAML、引用和 SOPS 文件规则，校验 OpenTofu，渲染全部 Kustomize 入口与 Helm chart，按对应 CRD schema 校验资源，并运行 Kuma 导出测试。

工具版本为 OpenTofu 1.13.9、Kustomize 5.8.3、Helm 3.19.2、Flux 2.9.6、kubeconform 0.8.0、Python 3 和 PyYAML。凭据操作使用 SOPS 3.13.3、age 1.3.2。安装方式和校验和验证见 CI workflow。验证需要联网下载 provider、chart 和 schema。

目录调整还应比较调整前后的渲染对象及 Flux 归属。静态验证通过后，涉及部署行为的改动继续验收实际服务。协作约定见 [AGENTS.md](AGENTS.md)。

## 许可证

[Apache-2.0](LICENSE)。部署的第三方软件和面板遵循各自的许可证。
