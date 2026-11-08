#!/usr/bin/env bash
# Register and sync selected applications through Argo CD (never direct workloads).
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$ROOT/scripts/argocd/deploy.py" "$@"
