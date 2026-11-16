# CloudLab 从零部署

完成后，CVM 上的 k3s 节点应为 `Ready`，Flux 应能从 GitHub 同步并解密 Secret，`https://whoami.<domain>` 应返回 HTTP 200。首次安装 k3s、创建云资源、生成凭据和配置 GitHub 同步各做一次。后续应用变更通过 Git 提交，由 Flux 同步。

## 1. 准备 GitHub、主机和工具

- 选择 Flux 读取的 GitHub 仓库和 `main` 分支。参考部署使用 `fullstackjam/cloudlab`。如果源仓库尚未建立，先在 GitHub 创建仓库并推送 `main`。
- 管理机需要 Git、SSH、OpenTofu、kubectl、Flux 2.9.6、SOPS、age 和凭据管理工具。Flux 2.9.6 对应这里的 Kubernetes 1.34。若需创建 GitHub 仓库，再准备已登录的 GitHub CLI。运行仓库校验还需要 Kustomize、Helm、kubeconform 和带 PyYAML 的 Python 3。工具版本见 [README](../README.md#验证)。从仓库根目录运行 `./scripts/validate.sh`，确认静态校验通过。 [Flux 2.9 支持版本](https://fluxcd.io/blog/2026/06/flux-v2.9.0/#supported-versions)
- 准备 Ubuntu 24.04 主机及其 SSH 和 sudo 访问。确认 ext4 数据盘已挂载到 `/data`，并核对系统盘、数据盘归属及开机挂载。新主机需先创建 CVM、配置 SSH 和安全组、附加并挂载数据盘。在部署配置中记录 SSH 目标、CVM ID 和数据盘 ID。
- CVM 需要访问 GitHub、Helm 仓库、镜像仓库及 Cloudflare。保留可信来源的 SSH 入口，Kubernetes API 由 SSH 转发访问。Tunnel 所需出站端口见 [DNS 与访问控制](dns.md)。
- 按 [凭据清单](secrets.md) 准备腾讯云和 Cloudflare 的 OpenTofu 凭据、Grafana 管理员和 InfluxDB 初始化凭据。GitHub fine-grained PAT 限定到 Flux 源仓库，`Contents` 和 `Administration` 均需读写权限，`Metadata` 只读。Flux 用这个 PAT 写入引导提交并登记 deploy key，仓库规则需允许引导提交写入 `main`。 [Flux GitHub bootstrap](https://fluxcd.io/flux/installation/bootstrap/github/)

## 2. 导入 CVM，创建 Tunnel

在管理机的仓库根目录运行。已有部署先取回对应的 OpenTofu state 和变量，核对当前资源。首次部署时复制变量示例，填写腾讯云控制台中核实的 CVM、镜像、VPC 和子网 ID，以及备份桶名、受信任的 SSH CIDR、Cloudflare Account ID。备份桶名使用 COS 凭据对应的 APPID 后缀。

在首次 plan 和部署前，修改 [集群配置](../infra/flux/settings.yaml) 中的 `CLOUDLAB_DOMAIN`。填入域名本身，例如 `example.com`。OpenTofu 用它生成 Tunnel ingress，Flux 用它生成 HTTPRoute、`HOMEPAGE_ALLOWED_HOSTS` 和 Homepage 链接。

CVM 和安全组凭据通过 `TENCENTCLOUD_SECRET_ID` 和 `TENCENTCLOUD_SECRET_KEY` 注入。COS 桶管理凭据通过 `TF_VAR_cos_secret_id` 和 `TF_VAR_cos_secret_key` 单独注入。OpenTofu 通过 `CLOUDFLARE_API_TOKEN` 读取 Tunnel 管理 token。DNS 记录走 [dns-config](https://github.com/fullstackjam/dns-config)。

```bash
umask 077
cp -n infra/terraform.tfvars.example infra/terraform.tfvars
tofu -chdir=infra init -backend=false
# Inject both Tencent credential sets and CLOUDFLARE_API_TOKEN before planning.
tofu -chdir=infra plan -out=review.tfplan
tofu -chdir=infra show -no-color review.tfplan
```

审查 plan。现有 CVM 应只导入，且没有实例或磁盘替换、删除，也没有 DNS 变更。首次创建的资源包括未附加到 CVM 的安全组、私有 COS 桶和远程管理的 Tunnel。仓库中的 CVM 定义用于导入。若从一台新 CVM 开始，先在腾讯云完成主机准备，再用它的真实 ID 生成导入计划。

如果备份桶已经创建且尚未登记到这份 state，先用 `tofu -chdir=infra import tencentcloud_cos_bucket.backups "$CLOUDLAB_COS_BUCKET"` 导入，再生成 plan。`CLOUDLAB_COS_BUCKET` 填带 APPID 后缀的完整桶名，与 `backup_bucket` 一致。核对导入后的 plan 继续使用同一个桶。

审查通过后运行 `tofu -chdir=infra apply review.tfplan`，并安全保存 state。用 `tofu -chdir=infra output -raw tunnel_id` 确认 Tunnel ID。在 Cloudflare 控制台打开对应 Tunnel，从 **Add a replica** 取 connector token，存入凭据库，稍后写入 `cloudflared` Secret。连接器由 Flux 部署到集群。 [Cloudflare Tunnel API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/)

## 3. 在 CVM 上安装 k3s

现有 CVM 按以下步骤手动安装。新主机若在创建时已使用 [cloud-init](../infra/cloud-init.yaml)，先完成本节的磁盘、安全组和 systemd 准备，再运行 `sudo /usr/local/sbin/cloudlab-install`，然后验收节点。如果盘上已有 `/data/k3s/server`，先确认要恢复原集群还是重建，并在启动前处理已有的集群状态。复用磁盘时只允许一台 k3s server 使用该目录。

```bash
mountpoint -q /data && findmnt -n -o TARGET,SOURCE,FSTYPE,UUID /data
df -h /data
systemctl show k3s -p LoadState -p ActiveState
```

核对 `/data` 的 UUID 与部署配置记录的 ext4 数据盘一致。如果系统盘重装后原盘仍附加，用 `lsblk -f` 核对 UUID。确认目标目录为空且尚未挂载，再用 `sudo mount -t ext4 UUID=<data-disk-uuid> /data` 挂载原盘。检查 `/etc/fstab` 里的 `/data` 条目，缺失时按实际 UUID 补上，并用 `sudo findmnt --verify` 校验。

```fstab
UUID=<data-disk-uuid> /data ext4 defaults,nofail 0 2
```

在启动 k3s 前写入 systemd drop-in，使 k3s 启动前先挂载 `/data`。现有主机和通过 cloud-init 准备的新主机都适用。

```bash
sudo install -d -m 0755 /etc/systemd/system/k3s.service.d
printf '[Unit]\nRequiresMountsFor=/data\n' |
  sudo tee /etc/systemd/system/k3s.service.d/data-mount.conf > /dev/null
sudo systemctl daemon-reload
```

在腾讯云账号检查实例当前安全组，确认公网入站仅放行所需端口，Kubernetes API 通过 SSH 转发访问。使用现有 CVM 的手动安装路径时，确认 k3s 尚未安装，再执行以下命令。

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates jq restic sqlite3
sudo install -d -m 0700 /etc/rancher/k3s
sudo install -d /data/k3s/storage
sudoedit /etc/rancher/k3s/config.yaml
```

将 [cloud-init](../infra/cloud-init.yaml) 中 `/etc/rancher/k3s/config.yaml` 对应的 `content` 写入配置文件。确认禁用内置 Traefik 和 ServiceLB，存储目录为 `/data/k3s/storage`，k3s 数据目录为 `/data/k3s/server`。

```bash
sudo chmod 0600 /etc/rancher/k3s/config.yaml
sudo curl -fsSL https://get.k3s.io -o /root/install-k3s.sh
sudo less /root/install-k3s.sh
sudo env INSTALL_K3S_VERSION=v1.34.1+k3s1 sh /root/install-k3s.sh server
```

当前配置使用主机名作为节点名。等待节点注册后，再等待 `Ready`。

```bash
node_name=$(hostname -s | tr '[:upper:]' '[:lower:]')
sudo k3s kubectl wait --for=create "node/$node_name" --timeout=120s
sudo k3s kubectl wait --for=condition=Ready "node/$node_name" --timeout=300s
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get storageclass local-path
```

节点应为 `Ready`，并有 `local-path` StorageClass。如果启动失败，先查 `sudo journalctl -u k3s -n 100 --no-pager`。

## 4. 准备 kubeconfig 和 SOPS 密文

在 CVM 上给当前 SSH 用户创建一个权限为 `0600` 的 kubeconfig 副本。该文件拥有集群管理员权限。

```bash
mkdir -p "$HOME/.kube"
chmod 0700 "$HOME/.kube"
sudo install -m 0600 -o "$(id -un)" -g "$(id -gn)" \
  /etc/rancher/k3s/k3s.yaml "$HOME/.kube/cloudlab.yaml"
```

在管理机的仓库根目录，通过 SSH 复制文件并启动转发。k3s 的默认 kubeconfig 指向 `https://127.0.0.1:6443`，所以本地转发使用相同地址和端口。保留转发终端，后续命令在另一个终端执行。k3s 更新客户端证书后，重新复制 kubeconfig。 [k3s 集群访问](https://docs.k3s.io/cluster-access)

```bash
export CLOUDLAB_SSH=ubuntu@cvm.example.net  # Replace with your SSH target.
umask 077
mkdir -p .local
scp "$CLOUDLAB_SSH:.kube/cloudlab.yaml" .local/kubeconfig-cloudlab
chmod 0600 .local/kubeconfig-cloudlab
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:6443:127.0.0.1:6443 "$CLOUDLAB_SSH"
```

首次部署按 [SOPS 加密步骤](secrets.md#生成并提交密文) 生成和备份 age 身份，填齐三个应用 Secret，再推送密文和它的 Kustomize 引用。重建时使用 Git 中已有的密文与对应的 age 私钥。若 Tunnel connector token 已轮换，先更新密文并推送。

如果从已部署的仓库复制源码到另一个 GitHub 仓库，先在副本里把 `infra/flux/flux-system/gotk-components.yaml` 和 `gotk-sync.yaml` 恢复为注释占位文件，再用新部署的 age 公钥和凭据生成密文。推送这些改动后，Flux 会为新仓库生成控制器和同步清单。原仓库上的集群重建沿用已经提交的同步清单。

全新 InfluxDB 环境先从 `apps/kustomization.yaml` 移除 Apple Health 接收器，并从 `infra/flux/secrets/kustomization.yaml` 移除 `apps/apple-health-influxdb/secrets` 的目录引用，从 `platform/monitoring/secrets/kustomization.yaml` 移除 Grafana Apple Health 数据源的密文引用。InfluxDB 就绪后，按 [Apple Health 接入](apple-health.md#iphone-自动导入与-grafana) 生成新 token，添加密文和引用，再启用接收器和数据源。

副本部署按 [监控与长期存储](monitoring.md) 准备 Thanos 的 COS 前缀和凭据，再用本次部署的 age 公钥加密对象存储配置。

按同一份监控文档填写 Alertmanager 的通知配置，并用本次部署的 age 公钥重新加密。

## 5. 执行一次 Flux bootstrap

在管理机的仓库根目录，先用 kubeconfig 核对节点，确认 SSH 转发连接的是目标 CVM。GitHub PAT 通过 `GITHUB_TOKEN` 注入。通过 `SOPS_AGE_KEY_FILE` 提供私钥文件，或注入 `SOPS_AGE_KEY`。下面示例使用私钥文件，凭据库注入示例见 [凭据清单](secrets.md#注入引导凭据)。

```bash
export KUBECONFIG="$PWD/.local/kubeconfig-cloudlab"
kubectl get nodes -o wide
GITHUB_REPOSITORY=fullstackjam/cloudlab SOPS_AGE_KEY_FILE="$PWD/.local/age.agekey" ./infra/bootstrap.sh
```

将 `GITHUB_REPOSITORY` 换成目标仓库的 `OWNER/REPO`。已有 GitHub `origin` 的 checkout 也支持只传仓库名，脚本从 `origin` 取 owner。组织仓库设置 `GITHUB_PERSONAL=false`。

脚本先本地验证密文可解，再创建命名空间和 `flux-system/sops-age`，最后安装 Flux、登记 deploy key，并把生成的 `gotk-components.yaml` 和 `gotk-sync.yaml` 提交到 GitHub。后续同步使用集群内的 deploy key。GitHub PAT 过期或撤销后，若 deploy key 失效，按官方流程轮换。 [Flux deploy key 轮换](https://fluxcd.io/flux/installation/bootstrap/github/#deploy-key-rotation)

```bash
git pull --ff-only
flux get sources git -A
flux get kustomizations -A
flux get helmreleases -A
kubectl get pods,pvc -A
kubectl get gateway,httproute -A
kubectl -n apps describe httproute whoami
kubectl -n infra logs deployment/cloudflared --tail=50
```

确认 GitRepository 同步到预期提交，`sources`、`gateway-crds`、`secrets`、`infra`、`apps` 均为 `Ready`，HelmRelease 已就绪且 PVC 已绑定。检查 Gateway 已编程，HTTPRoute 的 `Accepted` 和 `ResolvedRefs` 为 `True`，Cloudflare 控制台显示 Tunnel 已连接。

## 6. 验收公网入口和后续服务

按 [DNS 与访问控制](dns.md) 核对现有记录。在 `dns-config` 对应的区域文件中先为 `whoami` 加一条开启 Cloudflare 代理的 CNAME，目标是 `<TUNNEL_UUID>.cfargotunnel.com`。审查 PR 的 DNSControl preview，合并并发布后验证公网流量到达后端。

```bash
domain=example.com  # Use CLOUDLAB_DOMAIN from infra/flux/settings.yaml.
dig +short "whoami.$domain"
curl -fsS -w '\nHTTP %{http_code}\n' "https://whoami.$domain/"
```

预期 HTTP 200，响应包含 whoami 返回的主机信息。其余入口先配好对应 Access 策略，再逐个放行。随后初始化 Kuma，按 [Apple Health 接入](apple-health.md) 验证一次写入，并按 [备份指南](backups.md) 完成一次独立恢复演练后再开启定时备份。
