#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${GITHUB_TOKEN:?New GitHub token required}"
: "${GITHUB_REPOSITORY:?Set GITHUB_REPOSITORY to OWNER/REPO}"
: "${KUBECONFIG:?Set the kubeconfig path for CloudLab}"
github_repository=$GITHUB_REPOSITORY
if [[ $github_repository != */* ]]; then
  origin=$(git remote get-url origin 2>/dev/null || true)
  if [[ $origin =~ ^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/[^/]+/?$ ]]; then
    github_repository="${BASH_REMATCH[2]}/$github_repository"
  fi
fi
if [[ ! $github_repository =~ ^[a-zA-Z0-9-]+/[a-zA-Z0-9._-]+$ ]]; then
  echo 'Set GITHUB_REPOSITORY to OWNER/REPO or use a GitHub origin remote' >&2
  exit 1
fi
command -v flux >/dev/null
command -v kubectl >/dev/null
command -v sops >/dev/null
bootstrap_credentials=(
  platform/cloudflared/secrets/credentials.sops.yaml
  platform/monitoring/secrets/credentials.sops.yaml
  apps/influxdb/secrets/credentials.sops.yaml
)
if [[ -n ${SOPS_AGE_KEY_FILE:-} ]]; then
  age_key=$(< "$SOPS_AGE_KEY_FILE")
else
  age_key=${SOPS_AGE_KEY:?Set SOPS_AGE_KEY_FILE or inject SOPS_AGE_KEY}
fi
for credentials in "${bootstrap_credentials[@]}"; do
  [[ -f $credentials ]] || { echo "Encrypt $credentials first (docs/secrets.md)"; exit 1; }
  SOPS_AGE_KEY="$age_key" sops decrypt "$credentials" >/dev/null
done
flux check --pre
# Apply generated manifests server-side to keep bootstrap lightweight over SSH.
for ns in infra apps flux-system; do
  kubectl create namespace "$ns" --dry-run=client -o yaml |
    kubectl apply --server-side --validate=false -f -
done
printf '%s\n' "$age_key" |
  kubectl create secret generic sops-age -n flux-system --from-file=age.agekey=/dev/stdin --dry-run=client -o yaml |
  kubectl apply --server-side --validate=false -f -
unset age_key SOPS_AGE_KEY
flux bootstrap github --owner="${github_repository%/*}" --repository="${github_repository#*/}" \
  --branch=main --personal="${GITHUB_PERSONAL:-true}" --path=infra/flux --version=v2.9.6
flux reconcile kustomization flux-system --with-source
