#!/usr/bin/env bash
# Imports a remote cluster configuration through SSH and validates its endpoint
# before updating local configuration. VULTR_HOST is a historical variable name;
# it identifies the SSH destination, not a requirement to use that cloud vendor.

# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" || exit 1
# Usage: bash deploy/minikube/kubeconfig.sh --help
case "${1:-}" in --help|-h) echo 'Purpose: Run locally. Works with both `sh` and `bash`; never starts an SSH tunnel.'; echo 'Usage: bash deploy/minikube/kubeconfig.sh; see docs/operations/script-audit.md for prerequisites, variables and effects.'; exit 0;; esac
# Run locally. Works with both `sh` and `bash`; never starts an SSH tunnel.
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VULTR_HOST="${VULTR_HOST:-root@jithendar.com}"
API_HOST="${API_HOST:-jithendar.com}"
BASE_PROFILE="${PROFILE:-mk4}"
CONFIG="${CONFIG:-$HOME/.kube/config}"
fail() { echo "ERROR: $*" >&2; exit 1; }
case "$BASE_PROFILE" in ''|*[!a-zA-Z0-9_.-]*) fail 'Invalid PROFILE.' ;; esac
case "$VULTR_HOST" in -*|'') fail 'Invalid VULTR_HOST.' ;; esac
for tool in ssh kubectl python3; do command -v "$tool" >/dev/null 2>&1 || fail "$tool is required locally."; done
umask 077
mkdir -p "$(dirname "$CONFIG")"
TEMP_CONFIG_DIR="$(mktemp -d "$(dirname "$CONFIG")/.vultr-config.XXXXXX")"
trap 'rm -rf "$TEMP_CONFIG_DIR"' EXIT HUP INT TERM
# A single SSH connection discovers the selected profile and exports current
# embedded certificates. No dependency on an old *-direct.json export.
if ! ssh "$VULTR_HOST" bash -s -- "$BASE_PROFILE" > "$TEMP_CONFIG_DIR/source.json" <<'REMOTE'
set -euo pipefail
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
profile="$1"
state="${MINIKUBE_HOME:-$HOME/.minikube}/roboshop-installer/$profile.profile"
if [[ -f "$state" ]]; then profile="$(cat "$state")"; fi
[[ "$profile" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || { echo 'Invalid saved Minikube profile.' >&2; exit 1; }
[[ -f "${MINIKUBE_HOME:-$HOME/.minikube}/profiles/$profile/config.json" ]] || {
  echo "Minikube profile $profile does not exist for $(id -un). Check minikube profile list on Vultr." >&2; exit 1;
}
bindings="$(docker port "$profile" 8443/tcp)"
port="$(printf '%s\n' "$bindings" | awk -F: '/^0[.]0[.]0[.]0:/ {if ($NF == 32811) {print $NF; found=1; exit}; if (!first) first=$NF} END {if (!found && first) print first}' )"
[[ "$port" =~ ^[0-9]+$ ]] || {
  echo "Selected profile: $profile; actual API bindings: ${bindings:-none}" >&2
  echo "Profile $profile has no API port published on 0.0.0.0. Enable direct access with PUBLIC_HOST=jithendar.com using deploy/minikube/install.sh on Vultr." >&2
  echo 'Changing access settings may create a replacement cluster; existing workloads are not migrated.' >&2
  exit 1
}
kubectl --context="$profile" config view --minify --raw --flatten -o json | jq --arg port "$port" '{port: $port, config: .}'
REMOTE
then
  fail 'Remote export failed. Your existing local kubeconfig was not changed.'
fi
# Python validates the export and rewrites the endpoint while preserving TLS.
python3 "$SCRIPT_DIR/kubeconfig.py" \
  "$TEMP_CONFIG_DIR/source.json" "$TEMP_CONFIG_DIR/config.json" "$API_HOST"
chmod 600 "$TEMP_CONFIG_DIR/config.json"
# Validate before replacing a working config. No fallback to an old tunnel file.
if ! kubectl --kubeconfig="$TEMP_CONFIG_DIR/config.json" --request-timeout=15s get nodes; then
  fail 'Direct API check failed. Check DNS and allow its published TCP port from your local public IP in Vultr Firewall. Existing local config was preserved.'
fi
python3 "$SCRIPT_DIR/kubeconfig.py" --merge "$TEMP_CONFIG_DIR/config.json" "$CONFIG"
echo 'Minikube is now selected in the saved kubeconfig.'
if [ "$CONFIG" = "$HOME/.kube/config" ]; then
  echo 'Run: kubectl get nodes'
  echo 'Run: k9s'
  echo 'If KUBECONFIG is already exported in your terminal, run: unset KUBECONFIG'
else
  printf 'Custom destination: use --kubeconfig="%s" with kubectl or k9s.\n' "$CONFIG"
fi
