#!/usr/bin/env bash
# Installs the in-cluster ingress controller. Host port forwarding is deliberately
# a separate command, because Kubernetes ingress configuration alone does not
# make Docker-backed Minikube reachable from the public Internet.

# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" || exit 1
# Install the ingress controller; host routing is a separate, host-only step.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fail() { echo "ERROR: $*" >&2; exit 1; }
if [[ "${1:-}" == --help ]]; then
  echo 'Usage: bash deploy/minikube/deploy-traefik.sh'
  echo 'Overrides: PROFILE=mk4, KUBE_CONTEXT, TRAEFIK_CHART_VERSION=42.0.0'
  exit 0
fi
[[ $# == 0 ]] || fail 'No positional arguments expected.'
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
for command in helm kubectl; do
  command -v "$command" >/dev/null 2>&1 || fail "$command is required."
done
PROFILE="${PROFILE:-mk4}"
[[ "$PROFILE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Invalid PROFILE.'
if [[ -z "${KUBE_CONTEXT:-}" ]]; then
  state="${MINIKUBE_HOME:-$HOME/.minikube}/roboshop-installer/$PROFILE.profile"
  if [[ -f "$state" ]]; then PROFILE="$(cat "$state")"; fi
  KUBE_CONTEXT="$PROFILE"
fi
[[ "$KUBE_CONTEXT" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Invalid KUBE_CONTEXT.'
TRAEFIK_CHART_VERSION="${TRAEFIK_CHART_VERSION:-42.0.0}"
[[ "$TRAEFIK_CHART_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Invalid chart version.'
kubectl --context="$KUBE_CONTEXT" --request-timeout=15s get namespace kube-system >/dev/null
helm --kube-context="$KUBE_CONTEXT" upgrade --install traefik traefik \
  --repo https://traefik.github.io/charts --version "$TRAEFIK_CHART_VERSION" \
  --namespace traefik --create-namespace -f "$SCRIPT_DIR/traefik/values.yaml" \
  --wait --timeout 10m
kubectl --context="$KUBE_CONTEXT" get ingressclass traefik
kubectl --context="$KUBE_CONTEXT" -n traefik get service traefik
echo 'Traefik is ready inside Kubernetes. NodePorts: HTTP 30080, HTTPS 30443.'
echo 'On the Vultr Minikube host, run: bash deploy/minikube/expose-ingress.sh'
echo 'Manage applications and cert-manager through the argocd/ catalog. The shared jithendar.com certificate and TLSStore live in the jithendar.com repository.'
