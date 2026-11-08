#!/usr/bin/env bash
# Resolve the repository from this wrapper, even when invoked from another folder.
# Preview by default; --apply creates missing credentials without printing them.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$ROOT/scripts/setup/create_secrets.py" "$@"
