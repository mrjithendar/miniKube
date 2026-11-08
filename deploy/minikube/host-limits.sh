#!/usr/bin/env bash
# Docker-backed nodes share the host kernel. Increasing inotify capacity is a
# host-wide change, not a per-pod setting; inspect the proposed values and keep
# this operation separate from ordinary application deployment.

# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" linux || exit 1
# Usage: bash deploy/minikube/host-limits.sh --help
case "${1:-}" in --help|-h) echo 'Purpose: Docker-backed nodes share the Linux hosts inotify resources. Run on that host.'; echo 'Usage: bash deploy/minikube/host-limits.sh; see docs/operations/script-audit.md for prerequisites, variables and effects.'; exit 0;; esac
# Docker-backed nodes share the Linux host's inotify resources. Run on that host.
set -euo pipefail
fail() { echo "ERROR: $*" >&2; exit 1; }
command -v sysctl >/dev/null || fail 'sysctl is required (install procps/procps-ng).'
instances=$(sysctl -n fs.inotify.max_user_instances)
watches=$(sysctl -n fs.inotify.max_user_watches)
for value in "$instances" "$watches"; do
  [[ $value =~ ^[0-9]{1,10}$ ]] || fail 'Could not read valid host inotify limits.'
done
instances=$((10#$instances)); watches=$((10#$watches))
if (( instances >= 1024 && watches >= 1048576 )); then
  echo "Host inotify limits sufficient: instances=$instances watches=$watches"
  exit 0
fi
ADMIN=()
if [[ $EUID != 0 ]]; then
  command -v sudo >/dev/null || fail 'Rerun host-limits.sh as root to raise host inotify limits.'
  ADMIN=(sudo)
fi
# Never reduce higher existing values. Apply only this file, not unrelated sysctls.
config=$(mktemp)
trap 'rm -f "$config"' EXIT
printf 'fs.inotify.max_user_instances=%s\nfs.inotify.max_user_watches=%s\n' \
  "$((instances > 1024 ? instances : 1024))" \
  "$((watches > 1048576 ? watches : 1048576))" > "$config"
echo 'Raising host inotify limits to prevent fsnotify startup failures (sudo needed for non-root users).'
"${ADMIN[@]}" install -d -m 0755 /etc/sysctl.d
"${ADMIN[@]}" install -m 0644 "$config" /etc/sysctl.d/99-minikube-inotify.conf
"${ADMIN[@]}" sysctl -p /etc/sysctl.d/99-minikube-inotify.conf
(( $(sysctl -n fs.inotify.max_user_instances) >= 1024 && $(sysctl -n fs.inotify.max_user_watches) >= 1048576 )) || fail 'Host inotify limits did not take effect; cluster startup stopped.'
