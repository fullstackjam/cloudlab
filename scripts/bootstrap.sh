#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${GITHUB_TOKEN:?New GitHub token required}"
: "${GITHUB_REPOSITORY:?Set repository name under fullstackjam}"
: "${KUBECONFIG:?Set the kubeconfig path for CloudLab}"
command -v flux >/dev/null
command -v kubectl >/dev/null
command -v sops >/dev/null
[[ -f secrets/credentials.sops.yaml ]] || { echo 'Encrypt credentials first (docs/secrets.md)'; exit 1; }
if [[ -n ${SOPS_AGE_KEY_FILE:-} ]]; then
  age_key=$(< "$SOPS_AGE_KEY_FILE")
else
  age_key=${SOPS_AGE_KEY:?Set SOPS_AGE_KEY_FILE or inject SOPS_AGE_KEY}
fi
SOPS_AGE_KEY="$age_key" sops decrypt secrets/credentials.sops.yaml >/dev/null
flux check --pre
# Namespace must exist before actually creating the Secret.
for ns in infra apps flux-system; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done
printf '%s\n' "$age_key" |
  kubectl create secret generic sops-age -n flux-system --from-file=age.agekey=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -
unset age_key SOPS_AGE_KEY
flux bootstrap github --owner=fullstackjam --repository="$GITHUB_REPOSITORY" --branch=main --personal --path=clusters/cloudlab
flux reconcile kustomization flux-system --with-source
