#!/usr/bin/env bash
# DESTRUCTIVE: targets the current user's Minikube clusters and installer state.
# The confirmation is a data-loss boundary. It must remain separate from normal
# start/repair operations; cluster-local volumes may contain the only data copy.

# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" || exit 1
# Destructive reset of the current user's Minikube clusters and installer state.
set -euo pipefail
fail() { echo "ERROR: $*" >&2; exit 1; }
case "${1:-}" in
  --help|-h)
    echo 'Usage: bash deploy/minikube/reset.sh'
    echo 'Deletes ALL current-user Minikube clusters, cluster-local data, caches and installer configuration.'
    echo 'Keeps installed tools, unrelated Docker resources and unrelated Kubernetes contexts.'
    exit 0 ;;
  '') ;;
  *) fail 'No arguments expected.' ;;
esac
[[ $# -eq 0 ]] || fail 'No arguments expected.'
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
command -v minikube >/dev/null 2>&1 || fail 'minikube is required to delete cluster resources cleanly.'
[[ "$HOME" == /* && "$HOME" != / ]] || fail 'Invalid HOME.'
MK_HOME="${MINIKUBE_HOME:-$HOME/.minikube}"
# Purge recursively removes this directory. Reject broad or redirected paths.
[[ "$MK_HOME" == "$HOME/.minikube" && ! -L "$MK_HOME" ]] || fail 'Reset requires the standard non-symlink ~/.minikube directory; unset custom MINIKUBE_HOME.'
if grep -aq '^# RoboShop managed CLI wrapper$' "$(command -v minikube)"; then
  fail 'Run setup.sh --install-only first to replace the old forwarding wrapper; reset must use the current user.'
fi
exports=()
shopt -s nullglob
for entry in "$MK_HOME"/profiles/* "$MK_HOME"/roboshop-installer/*.network; do
  name="${entry##*/}"
  [[ "$entry" != *.network ]] || name="${name%.network}"
  [[ "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || continue
  exports+=("$HOME/.kube/$name-direct.json")
done
# Saved selections can outlive profiles deleted separately.
for entry in "$MK_HOME"/roboshop-installer/*.profile; do
  name=''
  IFS= read -r name < "$entry" || true
  [[ "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || continue
  exports+=("$HOME/.kube/$name-direct.json")
done
echo "Deleting ALL Minikube clusters and cluster-local data for $(id -un)."
# Let Minikube clean up its own containers, volumes and kubeconfig entries.
# If deletion fails, retain exported configuration for troubleshooting/retry.
minikube delete --all --purge
for exported in ${exports[@]+"${exports[@]}"}; do
  [[ ! -d "$exported" ]] || fail "Unexpected directory at $exported; not removed."
  rm -f -- "$exported"
done
echo 'Reset complete. Installed tools and unrelated Docker/Kubernetes configuration were retained.'
echo 'Start fresh: PUBLIC_HOST=jithendar.com NODES=1 bash deploy/minikube/setup.sh'
echo 'Previously copied laptop kubeconfigs are now obsolete; copy the new export after startup.'
