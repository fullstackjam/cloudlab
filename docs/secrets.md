# 凭据与 SOPS

Git 只保存 SOPS 密文和填写示例。明文凭据、age 私钥、kubeconfig 和 OpenTofu state 保存在凭据库或受控的本地目录。CVM 管理、COS 桶管理、备份和 Thanos 分别使用对应权限的凭据。

## 部署凭据

| 用途 | 内容 |
| --- | --- |
| SOPS | age 公钥与私钥；所有组件共用同一套集群解密身份 |
| GitHub bootstrap | 源仓库的 fine-grained PAT：Contents、Administration 读写，Metadata 只读；bootstrap 后由 deploy key 同步 |
| Cloudflare Tunnel 管理 | 目标账号的 Tunnel edit API token，注入 `CLOUDFLARE_API_TOKEN` |
| Cloudflare Tunnel 连接器 | connector token 和 Tunnel ID；连接器 token 写入 `cloudflared` Secret |
| CVM 与安全组 | `TENCENTCLOUD_SECRET_ID`、`TENCENTCLOUD_SECRET_KEY` |
| COS 桶管理 | `TF_VAR_cos_secret_id`、`TF_VAR_cos_secret_key` |
| COS 备份 | 备份前缀的对象列表、读写、删除和分片上传权限，及独立保存的 restic 密码 |
| COS 指标 | Thanos 指标前缀的读写、删除权限及所需的桶列表权限 |
| 资源恢复 | 加密保存的 OpenTofu state、仓库地址、实例和数据盘 ID |

## 应用 Secret

下表是当前资源引用的全部 Secret。新部署需要使用自己的凭据生成这些密文；不部署某个组件时，应同时移除其资源入口。

| Secret（命名空间/名称） | 密文路径 | 字段或用途 |
| --- | --- | --- |
| `infra/cloudflared` | `platform/cloudflared/credentials.sops.yaml` | `token` |
| `apps/grafana-admin` | `platform/kube-prometheus-stack/grafana-admin.sops.yaml` | `admin-user`、`admin-password` |
| `apps/cloudlab-alertmanager` | `platform/kube-prometheus-stack/alertmanager.sops.yaml` | `alertmanager.yaml`；见 [告警配置](monitoring.md#告警) |
| `apps/thanos-objstore` | `platform/thanos/objstore.sops.yaml` | `objstore.yml`；见 [对象存储凭据](monitoring.md#对象存储凭据) |
| `apps/influxdb-auth` | `apps/influxdb/credentials.sops.yaml` | 初始化 `password`、`token` |
| `apps/apple-health-ingester` | `apps/apple-health-influxdb/credentials.sops.yaml` | `auth-token`、`influx-write-token` |
| `apps/health-auto-export` | `apps/health-auto-export/credentials.sops.yaml` | `write-token`、`read-token`、`mongo-root-password`、`mongo-app-password` |
| `apps/grafana-health-auto-export-datasource` | `platform/kube-prometheus-stack/grafana-health-auto-export-datasource.sops.yaml` | 官方服务的读取 token；见 [Apple Health 接入](apple-health.md#health-auto-export-官方面板) |

InfluxDB 接收器的 `influx-write-token` 需要在 InfluxDB 初始化后创建，只授予 `apple-health` bucket 写权限。手机使用接收器的 `auth-token`，不使用 InfluxDB token。官方 Health Auto Export 使用另一套读写 token。

Kuma 的管理员和通知凭据在 UI 中初始化后保存到凭据库，当前不由 Kubernetes Secret 初始化。修改已有 InfluxDB 或 MongoDB 的登录凭据时，还需要在数据库内完成轮换；更新初始化 Secret 不会重置已有数据库。

## 生成并提交密文

从仓库根目录执行。首次部署生成 age 身份；重建原集群时，取回与 Git 密文匹配的私钥。

```bash
umask 077
mkdir -p .local
chmod 0700 .local
age-keygen -o .local/age.agekey
age-keygen -y .local/age.agekey
```

将公钥、私钥保存到凭据库，验证私钥可取回，并将公钥填写到 [.sops.yaml](../.sops.yaml) 的 `age` 字段。

下面以 Tunnel 凭据为例。其他 Secret 使用相应组件的示例或上表所列的字段，保持资源名称、命名空间和字段名与引用一致。

```bash
credential_dir=platform/cloudflared
cp "$credential_dir/credentials.example.yaml" .local/credentials.yaml
# Fill the placeholder values before running the next block.
```

```bash
sops encrypt --filename-override "$credential_dir/credentials.sops.yaml" \
  .local/credentials.yaml > .local/credentials.sops.yaml &&
SOPS_AGE_KEY_FILE="$PWD/.local/age.agekey" sops decrypt \
  .local/credentials.sops.yaml > /dev/null &&
mv .local/credentials.sops.yaml "$credential_dir/credentials.sops.yaml"
```

解密校验通过后删除 `.local/credentials.yaml`，在组件的 `kustomization.yaml` 中引用密文文件。platform 和 apps 的 [Flux 同步入口](../infra/flux/reconcile.yaml) 均启用 SOPS 解密。新增组件时，将它加入所属层的 `kustomization.yaml`。

检查待提交文件只有公钥、密文和资源引用，再提交到 Flux 读取的分支。公开仓库中的 Secret 名称和字段名仍然可见。Flux 使用 `flux-system/sops-age` 解密；bootstrap 后确认私钥可从凭据库恢复，即可删除本地私钥副本。[Flux SOPS 指南](https://fluxcd.io/flux/guides/mozilla-sops/)

## 注入引导凭据

`infra/bootstrap.sh` 需要 `KUBECONFIG`、`GITHUB_REPOSITORY`、`GITHUB_TOKEN`，以及 `SOPS_AGE_KEY_FILE` 或 `SOPS_AGE_KEY`。SSH 转发方式见 [部署手册](bootstrap.md#4-准备-kubeconfig-和-sops-密文)。

使用 1Password CLI 时，可建立 `CloudLab GitHub bootstrap`（字段 `token`）和 `CloudLab SOPS age`（字段 `private-key`）两个条目。将 `CLOUDLAB_VAULT` 设为所在 vault，执行：

```bash
unset SOPS_AGE_KEY_FILE
KUBECONFIG="$PWD/.local/kubeconfig-cloudlab" \
GITHUB_REPOSITORY=fullstackjam/cloudlab \
GITHUB_TOKEN="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab GitHub bootstrap/token" \
SOPS_AGE_KEY="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab SOPS age/private-key" \
op run -- ./infra/bootstrap.sh
```
