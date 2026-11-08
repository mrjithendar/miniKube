#!/usr/bin/env bash
# Reads the API target from CONFIG and prepares an SSH-forwarded endpoint.
# This is an explicit alternative to direct API access, not an automatic fallback;
# the SSH session must stay available while clients use the forwarded endpoint.

# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" linux || exit 1
VULTR_HOST=root@jithendar.com
# Usage: bash deploy/minikube/tunnel-endpoint.sh --help
case "${1:-}" in --help|-h) echo 'Purpose: tunnel-endpoint'; echo 'Usage: bash deploy/minikube/tunnel-endpoint.sh; see docs/operations/script-audit.md for prerequisites, variables and effects.'; exit 0;; esac

API_TARGET=$(python3 - "$CONFIG" <<'PY'
import json, sys
from urllib.parse import urlsplit

path = sys.argv[1]
with open(path) as f:
    config = json.load(f)

cluster = config["clusters"][0]["cluster"]
url = urlsplit(cluster["server"])
host = url.hostname
port = url.port or 443

cluster.setdefault("tls-server-name", host)
cluster["server"] = "https://127.0.0.1:16443"

with open(path, "w") as f:
    json.dump(config, f, indent=2)

print(f"[{host}]:{port}" if ":" in host else f"{host}:{port}")
PY
)

ssh -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -L "127.0.0.1:16443:$API_TARGET" \
  "$VULTR_HOST"