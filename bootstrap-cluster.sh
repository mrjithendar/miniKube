#!/usr/bin/env bash
# Run from the host or a Kubernetes client; all paths resolve against this repo.
# Installs controllers in dependency order before optional application deployment.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$ROOT/scripts/argocd/bootstrap.py" "$@"
