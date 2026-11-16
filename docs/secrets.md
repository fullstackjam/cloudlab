# Credentials and SOPS

Git stores only SOPS ciphertext and examples. Keep plaintext credentials, age private keys, kubeconfig, and OpenTofu state in a credential vault or a local directory with restricted access. Use separate credentials with the required permissions for CVM management, COS bucket management, backups, and Thanos.

## Deployment credentials

| Purpose | Required values |
| --- | --- |
| SOPS | An age public and private key pair; all components share one cluster decryption identity |
| GitHub bootstrap | A fine-grained PAT for the source repository: read/write Contents and Administration, read-only Metadata; a deploy key handles sync after bootstrap |
| Cloudflare Tunnel management | A Tunnel edit API token for the target account, passed as `CLOUDFLARE_API_TOKEN` |
| Cloudflare Tunnel connector | Connector token and Tunnel ID; store the connector token in the `cloudflared` Secret |
| CVM and security groups | `TENCENTCLOUD_SECRET_ID`, `TENCENTCLOUD_SECRET_KEY` |
| COS bucket management | `TF_VAR_cos_secret_id`, `TF_VAR_cos_secret_key` |
| COS backups | Object listing, read, write, delete, and multipart upload permissions for the backup prefix, plus a separately stored restic password |
| COS metrics | Read, write, and delete permissions for the Thanos metrics prefix, plus the required bucket listing permissions |
| Resource recovery | Encrypted OpenTofu state, repository URL, instance ID, and data disk ID |

## Application Secrets

The table lists all Secrets referenced by the current resources. Generate new ciphertext with your own credentials for a new deployment. If you omit a component, also remove its resource entry.

| Secret (namespace/name) | Encrypted file | Fields or purpose |
| --- | --- | --- |
| `infra/cloudflared` | `platform/cloudflared/credentials.sops.yaml` | `token` |
| `apps/grafana-admin` | `platform/kube-prometheus-stack/grafana-admin.sops.yaml` | `admin-user`, `admin-password` |
| `apps/cloudlab-alertmanager` | `platform/kube-prometheus-stack/alertmanager.sops.yaml` | `alertmanager.yaml`; see [alerts](monitoring.md#alerts) |
| `apps/thanos-objstore` | `platform/thanos/objstore.sops.yaml` | `objstore.yml`; see [object storage credentials](monitoring.md#object-storage-credentials) |
| `apps/influxdb-auth` | `apps/influxdb/credentials.sops.yaml` | Initial `password` and `token` |
| `apps/apple-health-ingester` | `apps/apple-health-influxdb/credentials.sops.yaml` | `auth-token`, `influx-write-token` |
| `apps/health-auto-export` | `apps/health-auto-export/credentials.sops.yaml` | `write-token`, `read-token`, `mongo-root-password`, `mongo-app-password` |
| `apps/grafana-health-auto-export-datasource` | `platform/kube-prometheus-stack/grafana-health-auto-export-datasource.sops.yaml` | Read token for the official service; see [Apple Health](apple-health.md#official-health-auto-export-dashboards) |

Create the InfluxDB receiver's `influx-write-token` after initializing InfluxDB. Grant it write access only to the `apple-health` bucket. The phone uses the receiver's `auth-token`, not the InfluxDB token. The official Health Auto Export service uses a separate pair of read and write tokens.

Set up Kuma's administrator and notification credentials in its UI, then save them in the credential vault. Kubernetes Secrets do not initialize them. To change login credentials for an existing InfluxDB or MongoDB instance, also rotate them inside the database. Updating the initialization Secret does not reset an existing database.

## Encrypt and commit secrets

Run these commands from the repository root. Generate an age identity for a new deployment. To rebuild an existing cluster, retrieve the private key that matches the ciphertext in Git.

```bash
umask 077
mkdir -p .local
chmod 0700 .local
age-keygen -o .local/age.agekey
age-keygen -y .local/age.agekey
```

Save both keys in the credential vault, verify that you can retrieve the private key, and put the public key in the `age` field of [.sops.yaml](../.sops.yaml).

The example below uses Tunnel credentials. For other Secrets, use the component's example or the fields listed above. Keep resource names, namespaces, and field names consistent with their references.

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

After the decryption check passes, delete `.local/credentials.yaml` and reference the encrypted file in the component's `kustomization.yaml`. Both the platform and apps [Flux sync entries](../infra/flux/reconcile.yaml) enable SOPS decryption. Add new components to their parent layer's `kustomization.yaml`.

Check that the files to commit contain only the public key, ciphertext, and resource references, then commit to the branch Flux watches. Secret names and field names remain visible in a public repository. Flux decrypts them using `flux-system/sops-age`. After bootstrap, verify that the private key can be recovered from the vault before deleting the local copy. See the [Flux SOPS guide](https://fluxcd.io/flux/guides/mozilla-sops/).

## Provide bootstrap credentials

`infra/bootstrap.sh` needs `KUBECONFIG`, `GITHUB_REPOSITORY`, `GITHUB_TOKEN`, and either `SOPS_AGE_KEY_FILE` or `SOPS_AGE_KEY`. See the [bootstrap guide](bootstrap.md#4-prepare-kubeconfig-and-encrypted-secrets) for SSH forwarding.

With the 1Password CLI, you can create items named `CloudLab GitHub bootstrap` with a `token` field and `CloudLab SOPS age` with a `private-key` field. Set `CLOUDLAB_VAULT` to their vault name, then run:

```bash
unset SOPS_AGE_KEY_FILE
KUBECONFIG="$PWD/.local/kubeconfig-cloudlab" \
GITHUB_REPOSITORY=fullstackjam/cloudlab \
GITHUB_TOKEN="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab GitHub bootstrap/token" \
SOPS_AGE_KEY="op://${CLOUDLAB_VAULT:?Set the vault name}/CloudLab SOPS age/private-key" \
op run -- ./infra/bootstrap.sh
```
