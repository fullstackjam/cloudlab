# DNS 与访问控制

基础域名由 [infra/flux/settings.yaml](../infra/flux/settings.yaml) 的 `CLOUDLAB_DOMAIN` 指定。以下域名使用开启 Cloudflare 代理的 CNAME，目标均为 `<TUNNEL_UUID>.cfargotunnel.com`。

| 域名 | 服务 |
| --- | --- |
| `whoami.<domain>` | 验证入口是否连通 |
| `status.<domain>` | Uptime Kuma 管理界面和公开状态页 |
| `grafana.<domain>` | Grafana |
| `influxdb.<domain>` | InfluxDB 管理界面和原生 API |
| `health-ingest.<domain>` | Health Auto Export 上传接口 |
| `home.<domain>` | Homepage |

InfluxDB 还保留 `health.<domain>` 兼容域名，新客户端使用 `influxdb.<domain>`。

## 添加域名

1. 在所属组件中配置 HTTPRoute，指定域名、路径和后端 Service，提交后等待 Flux 同步。
2. 在 [dns-config](https://github.com/fullstackjam/dns-config) 对应区域文件中添加代理 CNAME。参考部署的文件是 `domains/fullstackjam.com.js`，Tunnel ID 取自 `tofu -chdir=infra output -raw tunnel_id`。
3. 提交 PR，审查 DNSControl preview，确认变更只涉及预期记录后合并、发布。
4. 用 `dig` 检查解析，再通过 HTTPS 验证服务响应与认证行为。

Tunnel 已将 `*.<domain>` 转发到 Traefik，同一基础域名下增加服务只需 HTTPRoute 和 DNS 记录。更换基础域名时，先修改共享设置，应用 OpenTofu 中的 Tunnel 配置并等待新 HTTPRoute 就绪，再发布新 DNS 记录。

## 网络入口

TLS 在 Cloudflare 终止，Cloudflare 与 cloudflared 之间使用加密 Tunnel。cloudflared 通过集群内 HTTP 访问 Traefik。主机无需向公网开放 80、443 或 6443；远程管理 Kubernetes API 使用 [SSH 转发](bootstrap.md#4-准备-kubeconfig-和-sops-密文)。

OpenTofu 创建的安全组允许指定 IPv4 CIDR 的 SSH 入站和全部 IPv4 出站，但不会自动附加到 CVM。附加前核对现有安全组和 IPv6 规则，保留可用的 SSH 连接。Tunnel 需要 DNS 和 TCP/UDP 7844 出站，安装与更新还需要 HTTPS。

## 认证

仓库没有配置 Cloudflare Access。Grafana、InfluxDB 和 Kuma 使用应用自身的登录；Homepage 没有在本仓库中配置登录保护。需要限制这些入口时，可在 Cloudflare 配置 Access，域名解析和 Tunnel 本身不会提供身份认证。

`health-ingest.<domain>` 只转发以下上传请求，每个接收器验证自己的写入凭据：

| 请求 | 认证 |
| --- | --- |
| `POST /api/data` | `api-key` header，官方 Health Auto Export 写入 token |
| `POST /api/healthautoexport/v1/influxdb/ingest` | `Authorization: Bearer <token>`，InfluxDB 接收器 token |

官方服务的读取接口仅供 Grafana 在集群内访问。若给上传域名增加 Access，手机自动化还需提供相应的服务凭据。Kuma 公开状态页和管理界面共用域名，配置访问策略时需区分路径。
