#!/usr/bin/env bash
# Reruns a command when it failed only because a remote chart download hit a transient network or server error
# (e.g. "failed to fetch https://github.com/cloudnative-pg/charts/releases/download/...: 504 Gateway Timeout").
# Any other failure (bad values, a release that failed to deploy, a 404) is returned at once, unchanged.
#
#   scripts/lib/retry-chart-fetch.sh [-n attempts] [-w first_wait_seconds] -- command [args...]
#
# Defaults: 4 attempts, waiting 20 s, 40 s, 80 s between them. Used by .github/workflows/create-mk-infrastructure.yml.
set -uo pipefail

attempts=4
wait_s=20
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) attempts=$2; shift 2 ;;
    -w) wait_s=$2; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[[ $# -gt 0 ]] || { echo "usage: $0 [-n attempts] [-w seconds] -- command [args...]" >&2; exit 2; }

# Only download failures: never a Helm --wait timeout ("context deadline exceeded" alone), which must not be retried.
transient='failed to fetch .*: (429|5[0-9][0-9])|(502 Bad Gateway|503 Service Unavailable|504 Gateway Timeout)|TLS handshake timeout|Client\.Timeout exceeded while awaiting headers|connection reset by peer|dial tcp .*: i/o timeout|no such host|unexpected EOF'

log=$(mktemp)
trap 'rm -f "$log"' EXIT

for ((i = 1; i <= attempts; i++)); do
  "$@" 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  [[ $rc -eq 0 ]] && exit 0
  if ! grep -Eq "$transient" "$log"; then
    exit "$rc"
  fi
  if ((i == attempts)); then
    echo "::error::Chart download still failing after $attempts attempts"
    exit "$rc"
  fi
  echo "::warning::Transient chart download failure (attempt $i of $attempts); retrying in ${wait_s}s"
  sleep "$wait_s"
  wait_s=$((wait_s * 2))
done
