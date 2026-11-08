#!/usr/bin/env bash
# Supported repository entry point.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/deploy/minikube/deploy-traefik.sh" "$@"
