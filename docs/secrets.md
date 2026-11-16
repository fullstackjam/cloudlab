# 凭据清单与 SOPS
Keep deployment and application credentials in the credential store. Use separate credentials for CVM and security group access, COS bucket management, and backup uploads.

| Item | Fields / scope |
|---|---|
| CloudLab SOPS age | private-key, public-key; generate locally with `age-keygen` in ignored `.local/`, import private key into 1Password, verify recovery, remove local copy |
| CloudLab GitHub bootstrap | token; scoped to the CloudLab repo with Contents and Administration read/write, Metadata read-only. Track its expiry and the associated deploy key |
| CloudLab Cloudflare Tunnel Management | token; Cloudflare API token with Tunnel edit for the target account, passed to OpenTofu as `CLOUDFLARE_API_TOKEN` |
| CloudLab Cloudflare Tunnel Connector | token, tunnel-id; obtain from the created Tunnel, then encrypt the connector token into the `cloudflared` Secret |
| Tencent Cloud CVM | secret-id, secret-key; CVM describe/import and security group management |
| Tencent Cloud COS | secret-id, secret-key; COS bucket management |
| CloudLab COS restic | access-key-id, secret-access-key; only backup bucket/prefix list/get/put/delete/multipart; no account-wide permissions |
| CloudLab restic | password, repository; long random encryption password, preserve independently of COS |
| CloudLab InfluxDB | password, operator-token, health-write-token; operator for setup/export; separate bucket-write-only token for iOS |
| CloudLab Grafana | admin-user, admin-password |
| CloudLab Uptime Kuma | admin-user, admin-password, notification/API credentials; initialize UI later |
| CloudLab state recovery | encrypted OpenTofu state attachment, repository URL, disk/instance IDs; contains sensitive provider state |

## 生成并提交密文

从仓库根目录执行。首次部署生成新的 age 身份。重建时从凭据库取回与 Git 密文匹配的身份。

```bash
umask 077
mkdir -p .local
chmod 0700 .local
age-keygen -o .local/age.agekey
age-keygen -y .local/age.agekey
```

将公钥和私钥保存到 `CloudLab SOPS age` 条目，并验证能从凭据库取回私钥。在 [.sops.yaml](../.sops.yaml) 的 `age` 字段填入公钥。

密文与使用它的组件放在一起。三个启动必需的凭据分别是：

| Secret | 凭据目录 |
| --- | --- |
| cloudflared | `platform/cloudflared/secrets` |
| grafana-admin | `platform/monitoring/secrets` |
| influxdb-auth | `apps/influxdb/secrets` |

以下示例生成 Tunnel 的密文。对另外两个目录分别执行相同步骤，填入各自的凭据。

```bash
credential_dir=platform/cloudflared/secrets
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

加密和解密校验通过后，删除 `.local/credentials.yaml`。每个服务的 `secrets/kustomization.yaml` 引用该服务的密文文件；[Flux 解密入口](../infra/flux/secrets/kustomization.yaml) 引用这些目录。新增服务时，将它的密文目录加入该入口，普通资源入口不要重复引用 Secret。

检查待提交内容只有公钥、密文和资源引用，再提交并推送到 `main`。公开仓库中的 Secret 名称和字段名可见，私钥留在凭据库和 Flux 的解密 Secret 中。 [Flux SOPS 指南](https://fluxcd.io/flux/guides/mozilla-sops/)

回到 [部署手册](bootstrap.md#5-执行一次-flux-bootstrap)。完成 bootstrap 后，如果已确认可以从凭据库恢复私钥，可以删除本地私钥副本。OpenTofu state 作为加密附件存入凭据库。

## 注入引导凭据

在管理机的仓库根目录执行。以下命令需要 1Password CLI 和上表中的两个条目，并假设 SSH 转发已启动。将 `CLOUDLAB_VAULT` 设为存放两个引导凭据的 vault 名称。使用环境变量注入私钥时，先清除之前设置的 `SOPS_AGE_KEY_FILE`。

```bash
unset SOPS_AGE_KEY_FILE
KUBECONFIG="$PWD/.local/kubeconfig-cloudlab" \
GITHUB_REPOSITORY=fullstackjam/cloudlab \
GITHUB_TOKEN="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab GitHub bootstrap/token" \
SOPS_AGE_KEY="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab SOPS age/private-key" \
op run -- ./infra/bootstrap.sh
```
