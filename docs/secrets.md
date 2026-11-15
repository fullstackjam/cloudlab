# New 1Password inventory
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

将公钥和私钥保存到 `CloudLab SOPS age` 条目，并验证能从凭据库取回私钥。在 [.sops.yaml](../.sops.yaml) 的 `age` 字段填入公钥。将三个 Secret 的值填到本地副本，保留每个 Secret 之间的 `---`。Kustomize 按资源交给 Flux 解密，每个独立的 Secret 都要带 SOPS 元数据。

```bash
cp secrets/credentials.example.yaml .local/credentials.yaml
# Fill the placeholder values in the local copy before running the next block.
```

```bash
sops encrypt --filename-override secrets/credentials.sops.yaml \
  .local/credentials.yaml > .local/credentials.sops.yaml &&
SOPS_AGE_KEY_FILE="$PWD/.local/age.agekey" sops decrypt \
  .local/credentials.sops.yaml > /dev/null &&
mv .local/credentials.sops.yaml secrets/credentials.sops.yaml
```

加密和解密校验通过后，删除 `.local/credentials.yaml`。将 `secrets/kustomization.yaml` 的 `resources` 改为 `[credentials.sops.yaml]`，检查待提交内容只有公钥、密文和资源引用，再提交并推送到 `main`。

回到 [部署手册](bootstrap.md#5-执行一次-flux-bootstrap)。完成 bootstrap 后，如果已确认可以从凭据库恢复私钥，可以删除本地私钥副本。OpenTofu state 作为加密附件存入凭据库。

## 注入引导凭据

在管理机的仓库根目录执行。以下命令需要 1Password CLI 和上表中的两个条目，并假设 SSH 转发已启动。将 `CLOUDLAB_VAULT` 设为存放两个引导凭据的 vault 名称。使用环境变量注入私钥时，先清除之前设置的 `SOPS_AGE_KEY_FILE`。

```bash
unset SOPS_AGE_KEY_FILE
KUBECONFIG="$PWD/.local/kubeconfig-cloudlab" \
GITHUB_REPOSITORY=cloudlab \
GITHUB_TOKEN="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab GitHub bootstrap/token" \
SOPS_AGE_KEY="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab SOPS age/private-key" \
op run -- ./scripts/bootstrap.sh
```
