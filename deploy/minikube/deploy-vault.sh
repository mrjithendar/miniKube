#!/usr/bin/env bash
# Deploy persistent Vault using Helm, just like deploy-traefik.sh.
# Never initialize, unseal, rotate credentials or remove persistent data here.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KUBE_CONTEXT="${KUBE_CONTEXT:-${PROFILE:-mk4}}"
STORAGE_CLASS="${STORAGE_CLASS:-standard}"
case "${1:-}" in
  --help|-h)
    echo 'Usage: bash deploy/minikube/deploy-vault.sh [--dry-run]'
    echo 'Overrides: KUBE_CONTEXT=mk4 STORAGE_CLASS=standard'
    exit 0 ;;
  --dry-run)
    echo "Context: $KUBE_CONTEXT; Helm vault 0.34.1; namespace vault; storage $STORAGE_CLASS"
    echo 'Install/update persistent Vault and apply ingress: https://jithendar.com/vault -> /ui/'
    echo 'No initialization, unsealing, credential generation or cluster calls.'
    exit 0 ;;
  '') ;;
  *) echo 'Unknown option; use --help.' >&2; exit 1 ;;
esac
[[ $# == 0 ]] || exit 1
for tool in helm kubectl; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool is required." >&2; exit 1; }
done
kube=(kubectl --context="$KUBE_CONTEXT" --request-timeout=20s)
# Fail before installation if the existing ingress/TLS/storage prerequisites are missing.
"${kube[@]}" get ingressclass traefik >/dev/null
"${kube[@]}" get crd middlewares.traefik.io >/dev/null
"${kube[@]}" -n jithendar get tlsstore default >/dev/null
"${kube[@]}" -n jithendar wait certificate/jithendar-com --for=condition=Ready --timeout=30s
"${kube[@]}" get storageclass "$STORAGE_CLASS" >/dev/null
# No --wait: Vault legitimately stays unready until an operator unseals it.
# No --atomic: an unsealed-state wait must never roll back a persistent install.
helm --kube-context="$KUBE_CONTEXT" upgrade --install vault vault \
  --repo https://helm.releases.hashicorp.com --version 0.34.1 \
  --namespace vault --create-namespace \
  -f "$SCRIPT_DIR/vault/values.yaml" \
  --set-string "server.dataStorage.storageClass=$STORAGE_CLASS" --timeout 10m
"${kube[@]}" apply -k "$SCRIPT_DIR/vault"
echo 'Submitted: https://jithendar.com/vault (redirects to /ui/).'
echo 'Initialize once and unseal via the UI. Keep unseal keys and root token secure.'
echo 'Sealed/uninitialized is expected; existing data and credentials are preserved.'
