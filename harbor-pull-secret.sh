#!/usr/bin/env bash
# Create or replace Secret harbor-pull, the pull secret for registry.jithendar.com, in every active namespace of the
# cluster (run by an admin, never by CI). Safe to re-run: use it again to rotate the robot or cover new namespaces.
#   ./harbor-pull-secret.sh [kube-context]
# Uses Harbor robot robot$jithendar, which needs pull access to project jithendar, and prompts for its secret. The
# secret goes to curl and kubectl on stdin, never argv.
set -euo pipefail

CONTEXT="${1:-mk4}"
REGISTRY="registry.jithendar.com"
ROBOT_USER='robot$jithendar'

read -r -s -p "Secret for $ROBOT_USER: " ROBOT_SECRET; echo
[[ -n "$ROBOT_SECRET" ]] || { echo "The robot secret is required." >&2; exit 1; }

# Check the robot before storing it: a bad pull secret only shows up when a pod restarts. Harbor's token service
# answers 200 even for wrong credentials, with an empty action list, so check that the token grants pull.
granted="$(printf 'user = "%s:%s"\n' "$ROBOT_USER" "$ROBOT_SECRET" |
  curl -fsS --config - "https://$REGISTRY/service/token?service=harbor-registry&scope=repository:jithendar/jithendar-com:pull" |
  python3 -c 'import base64, json, sys
claims = json.load(sys.stdin)["token"].split(".")[1]
access = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4))).get("access") or []
print(" ".join(a for entry in access for a in entry.get("actions", [])))')"
[[ " $granted " == *" pull "* ]] || {
  echo "$ROBOT_USER cannot pull from project jithendar (wrong secret, or the robot lacks access). Nothing changed." >&2
  exit 1
}

# Terminating namespaces reject new objects, so only active ones get the secret.
NAMESPACES="$(kubectl --context "$CONTEXT" get namespaces --field-selector status.phase=Active \
  -o jsonpath='{.items[*].metadata.name}')"
[[ -n "$NAMESPACES" ]] || { echo "No active namespaces found in context $CONTEXT. Nothing changed." >&2; exit 1; }
echo "Namespaces: $NAMESPACES"

# One List, one apply: every namespace gets the same secret.
NAMESPACES="$NAMESPACES" ROBOT_USER="$ROBOT_USER" ROBOT_SECRET="$ROBOT_SECRET" REGISTRY="$REGISTRY" python3 - <<'EOF' |
import base64, json, os
auth = base64.b64encode(f"{os.environ['ROBOT_USER']}:{os.environ['ROBOT_SECRET']}".encode()).decode()
config = json.dumps({"auths": {os.environ["REGISTRY"]: {"auth": auth}}})
data = {".dockerconfigjson": base64.b64encode(config.encode()).decode()}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [
    {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/dockerconfigjson",
     "metadata": {"name": "harbor-pull", "namespace": namespace}, "data": data}
    for namespace in os.environ["NAMESPACES"].split()]}))
EOF
  kubectl --context "$CONTEXT" apply -f -

echo "Secret harbor-pull is ready in: $NAMESPACES"
