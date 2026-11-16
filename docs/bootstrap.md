# Bootstrap CloudLab

After setup, the CVM's k3s node should be `Ready`, Flux should sync from GitHub and decrypt Secrets, and `https://whoami.<domain>` should return HTTP 200. Installing k3s, creating cloud resources, generating credentials, and setting up GitHub sync are one-time steps. Later application changes are committed to Git and synced by Flux.

## 1. Prepare GitHub, the host, and tools

- Choose the GitHub repository and `main` branch that Flux will watch. The reference deployment uses `fullstackjam/cloudlab`. If the source repository does not exist, create it on GitHub and push `main` first.
- The management machine needs Git, SSH, OpenTofu, kubectl, Flux 2.9.6, SOPS, age, and a credential manager. Flux 2.9.6 supports Kubernetes 1.34 used here. To create a GitHub repository from the command line, also sign in to the GitHub CLI. Repository validation needs Kustomize, Helm, kubeconform, promtool, and Python 3 with PyYAML. See the [README](../README.md#validation) for versions. Run `./scripts/validate.sh` from the repository root and confirm that static checks pass. See [Flux 2.9 supported versions](https://fluxcd.io/blog/2026/06/flux-v2.9.0/#supported-versions).
- Prepare an Ubuntu 24.04 host with SSH and sudo access. Confirm that the ext4 data disk is mounted at `/data`. Check which disks are used for the system and data, and that the data disk mounts at boot. For a new host, first create the CVM, configure SSH and security groups, and attach and mount the data disk. Record the SSH target, CVM ID, and data disk ID in your deployment settings.
- The CVM needs access to GitHub, Helm repositories, container registries, and Cloudflare. Keep SSH access available from trusted sources and use SSH forwarding for the Kubernetes API. See [DNS and access control](dns.md) for Tunnel outbound ports.
- Use the [credentials list](secrets.md) to prepare Tencent Cloud and Cloudflare credentials for OpenTofu, a Grafana administrator account, and InfluxDB initialization credentials. Limit the GitHub fine-grained PAT to the Flux source repository, with read/write `Contents` and `Administration` permissions and read-only `Metadata`. Flux uses this PAT to push bootstrap commits and register a deploy key. Repository rules must allow bootstrap commits to `main`. See [Flux GitHub bootstrap](https://fluxcd.io/flux/installation/bootstrap/github/).

## 2. Import the CVM and create a Tunnel

Run these steps from the repository root on the management machine. For an existing deployment, retrieve its OpenTofu state and variables first and check the current resources. For a new deployment, copy the variables example and fill in the CVM, image, VPC, and subnet IDs verified in the Tencent Cloud console, along with the backup bucket name, trusted SSH CIDRs, and Cloudflare Account ID. The backup bucket name must use the APPID suffix associated with the COS credentials.

Before the first plan and deployment, set `CLOUDLAB_DOMAIN` in the [cluster settings](../infra/flux/settings.yaml) to your base domain, such as `example.com`. OpenTofu uses it for Tunnel ingress. Flux uses it for HTTPRoutes, `HOMEPAGE_ALLOWED_HOSTS`, and Homepage links.

Supply CVM and security group credentials through `TENCENTCLOUD_SECRET_ID` and `TENCENTCLOUD_SECRET_KEY`. Supply COS bucket management credentials separately through `TF_VAR_cos_secret_id` and `TF_VAR_cos_secret_key`. OpenTofu reads the Tunnel management token from `CLOUDFLARE_API_TOKEN`. DNS records are managed in [dns-config](https://github.com/fullstackjam/dns-config).

```bash
umask 077
cp -n infra/terraform.tfvars.example infra/terraform.tfvars
tofu -chdir=infra init -backend=false
# Inject both Tencent credential sets and CLOUDFLARE_API_TOKEN before planning.
tofu -chdir=infra plan -out=review.tfplan
tofu -chdir=infra show -no-color review.tfplan
```

Review the plan. It should only import the existing CVM, with no instance or disk replacement or deletion, and no DNS changes. Newly created resources include a security group that is not attached to the CVM, a private COS bucket, and a remotely managed Tunnel. The repository's CVM definition is intended for import. If starting with a new CVM, prepare the host in Tencent Cloud first, then use its actual ID to generate the import plan.

If the backup bucket already exists but is not in this state, import it with `tofu -chdir=infra import tencentcloud_cos_bucket.backups "$CLOUDLAB_COS_BUCKET"` before generating the plan. Set `CLOUDLAB_COS_BUCKET` to the full bucket name, including the APPID suffix, matching `backup_bucket`. Check that the plan continues to use the same bucket after import.

After reviewing the plan, run `tofu -chdir=infra apply review.tfplan` and store the state securely. Confirm the Tunnel ID with `tofu -chdir=infra output -raw tunnel_id`. Open that Tunnel in the Cloudflare dashboard and get its connector token from **Add a replica**. Save it in the credential vault for the `cloudflared` Secret. Flux deploys the connector into the cluster. See the [Cloudflare Tunnel API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/).

## 3. Install k3s on the CVM

For an existing CVM, follow the manual installation steps below. If a new host was created with [cloud-init](../infra/cloud-init.yaml), first complete the disk, security group, and systemd preparation in this section. Then run `sudo /usr/local/sbin/cloudlab-install` and verify the node. If `/data/k3s/server` already exists, decide whether to recover the original cluster or rebuild it, and handle the existing cluster state before starting. Only one k3s server may use that directory when reusing a disk.

```bash
mountpoint -q /data && findmnt -n -o TARGET,SOURCE,FSTYPE,UUID /data
df -h /data
systemctl show k3s -p LoadState -p ActiveState
```

Check that the UUID mounted at `/data` matches the ext4 data disk recorded in your deployment settings. If the system disk was reinstalled but the original data disk is still attached, check its UUID with `lsblk -f`. Confirm that the target directory is empty and not mounted, then mount the original disk with `sudo mount -t ext4 UUID=<data-disk-uuid> /data`. Check the `/data` entry in `/etc/fstab`. If missing, add it using the actual UUID, then validate with `sudo findmnt --verify`.

```fstab
UUID=<data-disk-uuid> /data ext4 defaults,nofail 0 2
```

Before starting k3s, add a systemd drop-in so `/data` is mounted first. This applies to both existing hosts and new hosts prepared with cloud-init.

```bash
sudo install -d -m 0755 /etc/systemd/system/k3s.service.d
printf '[Unit]\nRequiresMountsFor=/data\n' |
  sudo tee /etc/systemd/system/k3s.service.d/data-mount.conf > /dev/null
sudo systemctl daemon-reload
```

Check the instance's current security groups in Tencent Cloud. Allow only the required public inbound ports and access the Kubernetes API through SSH forwarding. For a manual installation on an existing CVM, confirm that k3s is not already installed before running:

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates jq restic sqlite3
sudo install -d -m 0700 /etc/rancher/k3s
sudo install -d /data/k3s/storage
sudoedit /etc/rancher/k3s/config.yaml
```

Copy the `content` for `/etc/rancher/k3s/config.yaml` from [cloud-init](../infra/cloud-init.yaml) into this file. Confirm that bundled Traefik and ServiceLB are disabled, the storage directory is `/data/k3s/storage`, and the k3s data directory is `/data/k3s/server`.

```bash
sudo chmod 0600 /etc/rancher/k3s/config.yaml
sudo curl -fsSL https://get.k3s.io -o /root/install-k3s.sh
sudo less /root/install-k3s.sh
sudo env INSTALL_K3S_VERSION=v1.34.1+k3s1 sh /root/install-k3s.sh server
```

The current configuration uses the hostname as the node name. Wait for node registration, then for `Ready`.

```bash
node_name=$(hostname -s | tr '[:upper:]' '[:lower:]')
sudo k3s kubectl wait --for=create "node/$node_name" --timeout=120s
sudo k3s kubectl wait --for=condition=Ready "node/$node_name" --timeout=300s
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl wait --for=create storageclass/local-path --timeout=120s
sudo k3s kubectl get storageclass local-path
```

The node should be `Ready`, and the `local-path` StorageClass should exist. If startup fails, check `sudo journalctl -u k3s -n 100 --no-pager` first.

## 4. Prepare kubeconfig and encrypted secrets

On the CVM, create a kubeconfig copy for the current SSH user with mode `0600`. This file grants cluster administrator access.

```bash
mkdir -p "$HOME/.kube"
chmod 0700 "$HOME/.kube"
sudo install -m 0600 -o "$(id -un)" -g "$(id -gn)" \
  /etc/rancher/k3s/k3s.yaml "$HOME/.kube/cloudlab.yaml"
```

From the repository root on the management machine, copy the file over SSH and start port forwarding. The default k3s kubeconfig points to `https://127.0.0.1:6443`, so the local forward uses the same address and port. Keep that terminal open and run later commands in another terminal. Copy kubeconfig again after k3s renews the client certificates. See [k3s cluster access](https://docs.k3s.io/cluster-access).

```bash
export CLOUDLAB_SSH=ubuntu@cvm.example.net  # Replace with your SSH target.
umask 077
mkdir -p .local
scp "$CLOUDLAB_SSH:.kube/cloudlab.yaml" .local/kubeconfig-cloudlab
chmod 0600 .local/kubeconfig-cloudlab
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:6443:127.0.0.1:6443 "$CLOUDLAB_SSH"
```

For a new deployment, follow the [SOPS steps](secrets.md#encrypt-and-commit-secrets) to generate and back up an age identity. Prepare Secrets for every enabled component using the credentials list, then push the encrypted files and their Kustomize references. For a rebuild, use the existing ciphertext in Git and its matching age private key. If the Tunnel connector token has been rotated, update and push its encrypted file first.

If copying an already deployed repository into a different GitHub repository, replace `infra/flux/flux-system/gotk-components.yaml` and `gotk-sync.yaml` in the copy with comment-only placeholders. Generate encrypted Secrets using the new deployment's age public key and credentials. Push these changes before bootstrap generates controller and sync manifests for the new repository. A cluster rebuild using the original repository keeps the existing committed sync manifests.

If you need the InfluxDB import path in a fresh environment, first remove the Apple Health receiver directory from `apps/kustomization.yaml`. This also disables its encrypted credentials. Once InfluxDB is ready, follow [automatic iPhone imports into InfluxDB](apple-health.md#automatic-iphone-imports-into-influxdb) to create the write token, add the encrypted credentials and references, and re-enable the receiver.

Use the [monitoring guide](monitoring.md) to prepare Thanos COS credentials and Alertmanager notification settings. To enable the official Health Auto Export service, also prepare MongoDB, API, and Grafana data source credentials using its [setup guide](apple-health.md#official-health-auto-export-dashboards). Encrypt all Secrets with this deployment's age public key.

## 5. Run Flux bootstrap once

From the repository root on the management machine, check the node through kubeconfig and confirm that SSH forwarding reaches the intended CVM. Supply the GitHub PAT through `GITHUB_TOKEN`. Provide the age private key through `SOPS_AGE_KEY_FILE` or `SOPS_AGE_KEY`. The example below uses a key file; see [bootstrap credentials](secrets.md#provide-bootstrap-credentials) for a vault-based example.

```bash
export KUBECONFIG="$PWD/.local/kubeconfig-cloudlab"
export CLOUDLAB_KUBE_CONTEXT=default
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" get nodes -o wide
GITHUB_REPOSITORY=fullstackjam/cloudlab SOPS_AGE_KEY_FILE="$PWD/.local/age.agekey" ./infra/bootstrap.sh
```

Replace `GITHUB_REPOSITORY` with the target repository's `OWNER/REPO`. A checkout with a GitHub `origin` also accepts just the repository name; the script reads the owner from `origin`. Set `GITHUB_PERSONAL=false` for an organization repository.

`GITHUB_BRANCH` defaults to `main`. For a recovery drill on another branch, update the GitRepository URL and branch in `infra/flux/flux-system/gotk-sync.yaml`, commit all isolation settings, and push that branch **before** running bootstrap. Pass the same branch through `GITHUB_BRANCH`. The script rejects a source mismatch before changing the cluster. An existing sync manifest can otherwise redirect the first reconciliation to the original branch, starting production connectors or writers on the recovery node.

The script first checks locally that the three base Secrets (Tunnel, Grafana, and InfluxDB) can be decrypted. It then creates namespaces and `flux-system/sops-age`, installs Flux, registers a deploy key, and commits the generated `gotk-components.yaml` and `gotk-sync.yaml` to GitHub. Later syncs use the deploy key stored in the cluster. If that key becomes invalid after the GitHub PAT expires or is revoked, follow the official [deploy key rotation procedure](https://fluxcd.io/flux/installation/bootstrap/github/#deploy-key-rotation).

```bash
git pull --ff-only
flux --context "$CLOUDLAB_KUBE_CONTEXT" get sources git -A
flux --context "$CLOUDLAB_KUBE_CONTEXT" get kustomizations -A
flux --context "$CLOUDLAB_KUBE_CONTEXT" get helmreleases -A
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" get pods,pvc -A
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" get gateway,httproute -A
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" -n apps describe httproute whoami
kubectl --context "$CLOUDLAB_KUBE_CONTEXT" -n infra logs deployment/cloudflared --tail=50
```

Confirm that the GitRepository has synced the intended commit, `gateway-crds`, `platform`, and `apps` are all `Ready`, HelmReleases are ready, and PVCs are bound. Check that the Gateway is programmed, HTTPRoute conditions `Accepted` and `ResolvedRefs` are `True`, and the Cloudflare dashboard shows the Tunnel as connected.

## 6. Verify public access and remaining services

Check existing records using [DNS and access control](dns.md). In the appropriate `dns-config` zone file, first add a Cloudflare-proxied CNAME for `whoami`, pointing to `<TUNNEL_UUID>.cfargotunnel.com`. Review the pull request's DNSControl preview, merge and publish it, then verify that public traffic reaches the backend.

```bash
domain=example.com  # Use CLOUDLAB_DOMAIN from infra/flux/settings.yaml.
dig +short "whoami.$domain"
curl -fsS -w '\nHTTP %{http_code}\n' "https://whoami.$domain/"
```

Expect HTTP 200 with host information from whoami. Review [authentication](dns.md#authentication) for the remaining endpoints before publishing their DNS records. Then initialize Kuma, verify one upload using the [Apple Health guide](apple-health.md), and complete an isolated restore using the [backup guide](backups.md) before enabling scheduled backups.
