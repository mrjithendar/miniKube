#!/usr/bin/env bash
# Creates or updates Secret postgres-db/postgres-backup-r2: the R2 API token postgres-cluster's backups use
# (deploy/minikube/postgres/cluster.yaml, "backups"; minikube-infrastructure/postgres.tf, var.postgres_backups).
#
# In the Cloudflare dashboard first: R2 > Create bucket (e.g. jithendar-postgres-backups, no public access), then
# R2 > Manage API tokens > Create API token: "Object Read & Write", applied to that bucket only. Copy the Access Key ID
# and Secret Access Key (shown once). Then:
#
#   scripts/postgres/backup-secret.sh            # prompts for both keys (nothing is echoed or stored in history)
#
# Uses the current kubectl context unless KUBE_CONTEXT is set (KUBE_CONTEXT=mk4 scripts/postgres/backup-secret.sh).
# Afterwards set postgres_backups in minikube-infrastructure/vars/mk4.tfvars and apply.
set -euo pipefail

ctx=()
[[ -n "${KUBE_CONTEXT:-}" ]] && ctx=(--context "$KUBE_CONTEXT")
echo "kubectl context: ${KUBE_CONTEXT:-$(kubectl config current-context)}"

read -r -p "R2 Access Key ID: " key_id
read -r -s -p "R2 Secret Access Key: " secret_key; echo
[[ ${#key_id} -ge 20 && ${#secret_key} -ge 40 ]] || { echo "Those do not look like R2 keys (ID 32 chars, secret 64)." >&2; exit 1; }

# Through the environment and stdin only: the keys never appear on a command line (ps) or in a file.
KEY_ID="$key_id" SECRET_KEY="$secret_key" python3 -I -c '
import base64, json, os
b = lambda v: base64.b64encode(v.encode()).decode()
print(json.dumps({
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": "postgres-backup-r2", "namespace": "postgres-db",
                 # The operator reloads the credentials when this Secret changes.
                 "labels": {"cnpg.io/reload": "true", "app.kubernetes.io/part-of": "postgres-db"}},
    "data": {"ACCESS_KEY_ID": b(os.environ["KEY_ID"]), "ACCESS_SECRET_KEY": b(os.environ["SECRET_KEY"])},
}))' | kubectl "${ctx[@]}" apply -f -
echo "Secret postgres-db/postgres-backup-r2 is ready. Next: postgres_backups in vars/mk4.tfvars, then terraform apply."
