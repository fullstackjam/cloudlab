# Jam CloudLab
Reusable single-node GitOps scaffold on an existing Tencent Cloud CVM, with a dedicated COS backup bucket. The Flux source is the private `fullstackjam/cloudlab` repository on `main`.

The reference deployment uses one Ubuntu 24.04 node with a separate ext4 data disk mounted at `/data`. Keep deployment-specific host addresses, infrastructure state and credentials in local configuration or in a private deployment repository.

Architecture: GitHub → Flux → SOPS/age Secrets → Traefik/Gateway API, Cloudflare Tunnel, Homepage, Uptime Kuma, whoami, InfluxDB/Apple Health and kube-prometheus-stack/Grafana. Single k3s server, built-in local-path provisioner on `/data/k3s/storage`. No Longhorn, Rook, Ceph, Harbor, Gitea, Kanidm or Woodpecker. Prometheus keeps 3 days / 8 GB within a 12 Gi PVC and has a 2 GiB memory limit. PVC requests are not disk quotas with local-path: monitor the 200 GB filesystem and alert before 80%. Size and upgrade node deliberately; single-node upgrades cause downtime. k3s server data is on `/data/k3s/server`; rebuilding uses Git + 1Password + data exports, not stale etcd copies.

Repository:

- `infra/`: OpenTofu providers, import-only CVM, unattached SG, protected private COS and a remotely managed Tunnel. DNS records are managed in [dns-config](https://github.com/fullstackjam/dns-config).
- `infra/cloud-init.yaml`: fresh-node base tools and manually gated k3s install. Never apply to this running machine.
- `clusters/cloudlab/`: staged Flux reconciliation, CRDs before infrastructure before apps.
- `platform/`, `apps/`: HelmReleases and plain Kubernetes manifests; versions pinned as a reviewable starting point.
- `secrets/`: inert example and empty Kustomization until real SOPS encryption; [.sops.yaml](.sops.yaml) needs a NEW public recipient.
- `scripts/`: bootstrap, local Git compatibility alias, validations and sanitized application backup/restore tools.

## Local Git and validation

The [Validate workflow](.github/workflows/validate.yml) runs on pull requests and pushes to `main`. It checks for tracked local deployment files, scans Git history and the checkout for credentials, and runs the same static validation as the local command below. The workflow needs public package and chart downloads and uses read-only repository access.

This checkout uses a normal writable `.git` on branch `main`. Use plain `git` from this directory. `scripts/git-local` remains a compatibility alias for `git -C` at the repository root; it requires no alternate metadata directory.

The ubuntu user's CLIs are installed in `~/.local/bin`: OpenTofu 1.13.9, Kustomize 5.8.3, Helm 3.19.2, Flux 2.10.0, SOPS 3.13.3, age/age-keygen 1.3.2 and kubeconform 0.8.0. Official release downloads were SHA256 checked. Python 3 with PyYAML is also required. Run `./scripts/validate.sh` with network access: it checks shell/YAML/references, the SOPS filename rule, OpenTofu format/init/validate, all seven Kustomize roots, both pinned Helm charts (including CRDs), and an offline Flux controller export. kubeconform checks Kubernetes 1.34 schemas plus the exact rendered CRD schemas, with no missing-schema skips. Rendering targets the scaffold's k3s 1.34 version. No cluster connection or cloud credentials are needed. Temporary rendered files are removed automatically. Static validation does not verify live reconciliation, image pulls, storage or credentials.

## First deployment

Follow the [bootstrap runbook](docs/bootstrap.md) to prepare the CVM, cloud resources and SOPS credentials, initialize Flux, and verify the first public route.

## Rebuild and upgrades
Recover 1Password first (age identity, Git access, COS key, restic password, Terraform state and infrastructure IDs). On a deliberately provisioned replacement Ubuntu node, attach/mount `/data` without formatting, install the reviewed k3s configuration with built-in Traefik/ServiceLB disabled, bootstrap Flux from the same repository, restore sanitized application data using the isolated drill procedure, then restore UI credentials/integrations from 1Password. Keep old node stopped only after validating replacement, and explicitly decide whether a reused disk's old k3s server directory should be archived; never start two servers against it. Rotate the Tunnel token when rebuilding connector access. Review Terraform import/state separately before any cloud change.

Upgrade one pinned component per PR/change; check chart migration notes and Gateway API compatibility, run backups and isolated restore, inspect manifests/plan, then reconcile during a maintenance window. Do not auto-upgrade the node through this initial scaffold. Flux prune deletes removed resources; local-path data is not replicated and default reclaim policy may delete data when PVCs are removed. Protect PVCs during uninstall/migration and verify backups first.

References checked: [Tencent instance/import schema](https://registry.terraform.io/providers/tencentcloudstack/tencentcloud/latest/docs/resources/instance), [COS bucket](https://registry.terraform.io/providers/tencentcloudstack/tencentcloud/latest/docs/resources/cos_bucket), [Cloudflare Tunnel Terraform](https://developers.cloudflare.com/tunnel/guides/terraform/), [Traefik Gateway API](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/gateway-api/). Network downloads and static validation now work. Validation status is in SUMMARY.md; do not equate YAML parsing with a successful deployment.
