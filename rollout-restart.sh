#!/usr/bin/env bash
# Rollout-restart Deployments by name, across every namespace.
#
#   ./rollout-restart.sh 'moks*'              every Deployment whose name starts with moks
#   ./rollout-restart.sh moks-api 'jithendar*' several names or patterns
#   ./rollout-restart.sh                      every Deployment in the cluster (asks first)
#   ./rollout-restart.sh -n 'moks*'           list what would restart, change nothing
#   ./rollout-restart.sh -y 'moks*'           skip the confirmation
#
# Quote patterns ('moks*') so the shell does not expand them against local files.
# CONTEXT picks the kubectl context (default mk4).

# `sh rollout-restart.sh` runs POSIX sh (bash in posix mode on macOS), which lacks what this needs: rerun under bash.
case ":${SHELLOPTS:-}:" in *:posix:*) exec bash "$0" "$@" ;; esac
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

CONTEXT="${CONTEXT:-mk4}"
dry_run=false
yes=false
while getopts "ny" opt; do
  case "$opt" in
    n) dry_run=true ;;
    y) yes=true ;;
    *) sed -n '2,11p' "$0"; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
patterns=("$@")
[ ${#patterns[@]} -eq 0 ] && patterns=('*')

matches=()
while read -r ns name; do
  for p in "${patterns[@]}"; do
    # shellcheck disable=SC2053  # unquoted on purpose: $p is a glob
    if [[ "$name" == $p ]]; then matches+=("$ns $name"); break; fi
  done
done < <(kubectl --context "$CONTEXT" get deploy -A --no-headers -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name)

if [ ${#matches[@]} -eq 0 ]; then
  echo "No Deployment matches: ${patterns[*]}" >&2
  exit 1
fi

echo "Context $CONTEXT, ${#matches[@]} Deployment(s):"
for m in "${matches[@]}"; do echo "  ${m/ //}"; done
$dry_run && exit 0

if ! $yes; then
  read -r -p "Restart them? [y/N] " answer
  [[ "$answer" == [yY]* ]] || { echo "Nothing restarted."; exit 0; }
fi

failed=0
for m in "${matches[@]}"; do
  read -r ns name <<<"$m"
  kubectl --context "$CONTEXT" -n "$ns" rollout restart "deploy/$name" || failed=$((failed + 1))
done
for m in "${matches[@]}"; do
  read -r ns name <<<"$m"
  kubectl --context "$CONTEXT" -n "$ns" rollout status "deploy/$name" --timeout=5m || failed=$((failed + 1))
done

if [ "$failed" -ne 0 ]; then echo "$failed step(s) failed." >&2; exit 1; fi
echo "All rolled out."
