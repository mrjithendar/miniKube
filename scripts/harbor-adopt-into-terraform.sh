#!/usr/bin/env bash
# One-time: hand Harbor's namespace and Secrets, created by the old scripts, over to Terraform (minikube-infrastructure/
# harbor.tf) without changing a single value. Run once from the repository root, then delete this file.
#   export TF_VAR_dockerhub_username=... TF_VAR_dockerhub_password=...   # the same Docker Hub login as DOCKER_*
#   bash scripts/harbor-adopt-into-terraform.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONTEXT="${KUBE_CONTEXT:-mk4}"
: "${TF_VAR_dockerhub_username:?export TF_VAR_dockerhub_username first}"
: "${TF_VAR_dockerhub_password:?export TF_VAR_dockerhub_password first}"
export DOCKER_CONFIG="$(mktemp -d)"   # OCI chart pulls (README.md)
tf() { terraform -chdir="$ROOT/minikube-infrastructure" "$@"; }
in_state() { tf state list 2>/dev/null | grep -qxF "$1"; }
import() {
  if in_state "$1"; then echo "already in state: $1"; return; fi
  tf import -var-file=vars/mk4.tfvars -input=false "$1" "$2" >/dev/null
  echo "imported: $1"
}

if ps aux | grep -v grep | grep -q 'terraform -chdir'; then
  echo 'Another terraform run is active: stop it first.' >&2; exit 1
fi

# The previous harbor.tf only labelled the namespace; forget that resource (no change on the cluster).
in_state kubernetes_labels.harbor_namespace && tf state rm kubernetes_labels.harbor_namespace >/dev/null && echo 'forgot: kubernetes_labels.harbor_namespace'

import kubernetes_namespace_v1.harbor harbor
import kubernetes_secret_v1.harbor_credentials harbor/harbor-credentials
import kubernetes_secret_v1.harbor_dockerhub_pull harbor/dockerhub-pull

# The generated passwords keep their current values: read from the Secret, passed straight to terraform, never printed.
for key in HARBOR_ADMIN_PASSWORD secretKey secret CSRF_KEY JOBSERVICE_SECRET REGISTRY_HTTP_SECRET REGISTRY_PASSWD; do
  address="random_password.harbor[\"$key\"]"
  in_state "$address" && { echo "already in state: $address"; continue; }
  value="$(kubectl --context "$CONTEXT" -n harbor get secret harbor-credentials -o jsonpath="{.data.$key}" | base64 -d)"
  [ -n "$value" ] || { echo "harbor-credentials has no $key" >&2; exit 1; }
  tf import -var-file=vars/mk4.tfvars -input=false "$address" "$value" >/dev/null && echo "imported: $address"
  unset value
done

echo
echo 'Done. Now: terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars'
echo 'Expected for Harbor: labels on the namespace and Secrets, harbor-credentials drops its unused "password" key and'
echo 'gets a new REGISTRY_HTPASSWD line for the same password; no password changes.'
