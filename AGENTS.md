# CloudLab contributor guide

See the [README](README.md#repository-layout) for directory ownership and entry points, and the [bootstrap guide](docs/bootstrap.md) for deployment steps.

## Node recovery

For a request to restore this environment on a specified machine, follow [the recovery workflow](docs/recovery.md) and use `infra/restore.py`. Discover the target, source node, disk, and existing credentials before asking for values that are already available. Carry the task through data verification and the requested cutover; do not stop after bootstrap or Pod readiness. Reuse the existing age identity, bucket, Tunnel, and domains. Keep recovery checkpoints and plaintext credentials outside Git, and do not depend on an earlier chat or temporary helper scripts.

## Scope of changes

- Write repository documentation, comments, and examples in plain English.
- Organize deployment configuration under `infra`, `platform`, and `apps`. Keep each component's manifests, encrypted secrets, and examples in its own directory. Host scripts and systemd units belong in `infra/backup`; the root `scripts` directory contains only repository validation tools and their tests.
- Each component declares its Helm repository, resources, routes, and Secrets through its own `kustomization.yaml`. The `platform` and `apps` sync entries each enable SOPS decryption and domain substitution. Add a separate sync stage only when a deployment prerequisite requires it.
- Flux bootstrap generates `infra/flux/flux-system/gotk-components.yaml` and `gotk-sync.yaml`. Check the bootstrap procedure when updating these files.
- Manage public DNS records in [dns-config](https://github.com/fullstackjam/dns-config).
- Keep configuration, examples, and SOPS ciphertext in Git. Keep plaintext credentials, age private keys, kubeconfig, OpenTofu state, and backup data outside Git. See the [credentials guide](docs/secrets.md) for encryption steps.

## Validation

- Run `./scripts/validate.sh` for changes to Kubernetes manifests, Helm values, or OpenTofu configuration. CI uses the same script. Tool versions are listed in the [README](README.md#validation).
- When reorganizing manifests, compare the rendered output of affected Kustomize entry points before and after the change. Before moving objects between Flux Kustomizations, check ownership, the handover order, and how `prune: true` affects PVCs and CRDs.
- Before upgrading a stateful service, check the upstream migration requirements and backup and restore procedure. After deployment, verify the Flux revision, workload readiness, and actual service behavior.
