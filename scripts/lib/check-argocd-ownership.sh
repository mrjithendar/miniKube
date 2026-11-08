#!/usr/bin/env bash
# Fail before an imperative deploy can compete with a registered Application.
set -euo pipefail
context="${1:?Kubernetes context required}"
shift
[[ $# -gt 0 ]] || { echo 'Application names required.' >&2; exit 2; }
crd="$(kubectl --context "$context" --request-timeout=15s get crd applications.argoproj.io --ignore-not-found -o name)"
[[ -n "$crd" ]] || exit 0
existing="$(kubectl --context "$context" --request-timeout=15s get applications "$@" -n argocd --ignore-not-found -o name)"
if [[ -n "$existing" ]]; then
  echo 'Deployment stopped: these workloads have Argo CD Applications. Update Git and sync in Argo CD instead of applying an imperative deployment:' >&2
  echo "$existing" >&2
  exit 1
fi
