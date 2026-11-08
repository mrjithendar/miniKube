#!/usr/bin/env bash
# Cluster lifecycle entry point. Tool installation delegates to init.sh in the tools repository
# (sibling checkout ../tools, or TOOLS_DIR).
# PROFILE identifies the cluster; saved installer state can select a previously
# fitted profile. NODES/CPUS/MEMORY affect capacity, while PUBLIC_HOST controls
# direct API access; neither should be changed casually on a populated cluster.

# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" || exit 1
# Docker-backed Minikube on supported Linux hosts and macOS (amd64/arm64).
set -euo pipefail
PROFILE="${PROFILE:-mk4}"
CPUS="${CPUS:-2}"
MEMORY="${MEMORY:-auto}" # MiB per node, selected after checking Docker capacity.
NODES="${NODES:-2}"
if [[ "$EUID" == 0 ]]; then DEFAULT_INSTALL_DIR=/usr/local/bin; else DEFAULT_INSTALL_DIR="$HOME/.local/bin"; fi
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
ACTION="${1:-start}"
BASE_PROFILE="$PROFILE"
PUBLIC_IP="${PUBLIC_IP:-}"
PUBLIC_HOST="${PUBLIC_HOST:-}"
API_HOST="${PUBLIC_HOST:-$PUBLIC_IP}"
API_PORT="${API_PORT:-32811}" # Stable host port; the API inside the node stays on 8443.
NETWORK_ARGS=()
fail() { echo "ERROR: $*" >&2; exit 1; }
usage() {
  echo 'Usage: bash deploy/minikube/install.sh [install|start|status|stop|diagnose|reset]'
  echo 'Overrides: PROFILE, NODES, CPUS, MEMORY (MiB/node), INSTALL_DIR'
  echo 'Direct access: PUBLIC_HOST=jithendar.com (or PUBLIC_IP); API_PORT defaults to 32811.'
  echo 'Runs as the current user; Linux root starts use --force.'
  echo 'Tool installation is centralized in the tools repository: ../tools/init.sh --profile minikube (TOOLS_DIR overrides);'
  echo 'versions live in tools/scripts/setup/versions.json.'
}
if [[ "$ACTION" == reset ]]; then
  [[ $# -eq 1 ]] || fail 'Usage: bash deploy/minikube/install.sh reset'
  exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/reset.sh"
fi
case "$ACTION" in -h|--help|help) usage; exit 0 ;; install|start|status|stop|diagnose) ;; *) usage >&2; exit 1 ;; esac
[[ $# -le 1 ]] || fail 'Expected at most one action.'
case "$(uname -s)" in Linux) OS=linux ;; Darwin) OS=darwin ;; *) fail 'Supported systems: Linux and macOS.' ;; esac
case "$(uname -m)" in x86_64|amd64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;; *) fail 'Supported architectures: amd64 and arm64.' ;; esac
ROOT_ARGS=()
if [[ "$EUID" == 0 ]]; then
  [[ "$OS" == linux ]] || fail 'On macOS, run as your normal user.'
  ROOT_ARGS=(--force)
fi
[[ "$INSTALL_DIR" == /* ]] || fail 'INSTALL_DIR must be an absolute path.'
export PATH="$INSTALL_DIR:$PATH"
if [[ "$ACTION" == install ]]; then
  TOOLS_DIR="${TOOLS_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)/tools}"
  [[ -f "$TOOLS_DIR/init.sh" ]] || fail "init.sh not found in $TOOLS_DIR: clone https://github.com/mrjithendar/tools.git beside this repository or set TOOLS_DIR."
  exec bash "$TOOLS_DIR/init.sh" --profile minikube
fi
for tool in minikube kubectl; do
  command -v "$tool" >/dev/null || fail "$tool is required. Run tools/init.sh --profile minikube first."
done
[[ "$BASE_PROFILE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Invalid PROFILE name.'
STATE_DIR="${MINIKUBE_HOME:-$HOME/.minikube}/roboshop-installer"
STATE_FILE="$STATE_DIR/$BASE_PROFILE.profile"
if [[ -f "$STATE_FILE" ]]; then
  IFS= read -r PROFILE < "$STATE_FILE"
  [[ "$PROFILE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Invalid saved profile name.'
fi
# A routine start must not silently change an existing direct-access cluster
# back to local-only just because the caller omitted the environment variables.
if [[ -z "$PUBLIC_HOST" && -z "$PUBLIC_IP" && -f "$STATE_DIR/$PROFILE.network" ]]; then
  saved_network="$(cat "$STATE_DIR/$PROFILE.network")"
  case "$saved_network" in
    dns-*) PUBLIC_HOST="${saved_network#dns-}" ;;
    direct-*) PUBLIC_IP="${saved_network#direct-}"; PUBLIC_IP="${PUBLIC_IP//-/.}" ;;
  esac
fi
API_HOST="${PUBLIC_HOST:-$PUBLIC_IP}"
command -v minikube >/dev/null 2>&1 || fail 'Run this script with install first.'
diagnose() {
  echo "=== Cluster $PROFILE ==="
  minikube --profile="$PROFILE" status || true
  kubectl --context="$PROFILE" --request-timeout=15s get nodes -o wide || true
  kubectl --context="$PROFILE" --request-timeout=15s get events -A --field-selector=type=Warning || true
  echo '=== Docker capacity and running containers ==='
  docker info --format 'CPUs={{.NCPU}} MemoryBytes={{.MemTotal}}' || true
  docker stats --no-stream || true
  if [[ "$OS" == linux ]]; then
    free -m || true; df -h / || true
    sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches || true
  fi
  echo '=== Minikube logs ==='
  minikube --profile="$PROFILE" logs --problems || true
}
if [[ "$ACTION" == diagnose ]]; then
  diagnose
  exit 0
fi
if [[ "$ACTION" != start ]]; then
  minikube --profile="$PROFILE" "$ACTION"
  exit 0
fi
for value in "$NODES" "$CPUS"; do
  [[ "$value" =~ ^[1-9][0-9]{0,5}$ ]] || fail 'NODES and CPUS must be positive integers.'
done
[[ "$MEMORY" == auto || "$MEMORY" =~ ^[1-9][0-9]{0,6}$ ]] || fail 'MEMORY must be auto or a positive MiB integer.'
DOCKER_APP=''
if [[ "$OS" == darwin ]]; then
  for candidate in /Applications/Docker.app "$HOME/Applications/Docker.app"; do
    if [[ -d "$candidate" ]]; then DOCKER_APP="$candidate"; break; fi
  done
  if [[ -n "$DOCKER_APP" ]]; then
    export PATH="$PATH:$DOCKER_APP/Contents/Resources/bin"
  fi
fi
if [[ "$OS" == linux && -r /etc/os-release ]]; then
  distro_id="$(. /etc/os-release; printf '%s' "${ID:-}")"
  if [[ "$distro_id" == centos ]]; then
    echo 'CentOS: for Docker prerequisites, run tools/init.sh --profile minikube (Stream 9/10).'
  fi
fi
command -v docker >/dev/null 2>&1 || fail 'Run tools/init.sh --profile minikube, then rerun. See README.md.'
if ! docker info >/dev/null 2>&1; then
  if [[ "$OS" == darwin && -n "$DOCKER_APP" ]]; then
    open -a "$DOCKER_APP" || fail 'Could not open Docker Desktop.'
    echo 'Waiting up to 180 seconds for Docker Desktop; complete any first-run prompts.'
    ready=0
    for ((i=0; i<90; i++)); do
      if docker info >/dev/null 2>&1; then ready=1; break; fi
      sleep 2
    done
    [[ "$ready" == 1 ]] || fail 'Docker is unavailable. Check Docker Desktop and your Docker context.'
  elif [[ "$OS" == darwin ]]; then
    echo "Docker context: $(docker context show 2>/dev/null || echo unknown)" >&2
    fail 'Docker CLI is installed, but its engine is unreachable and Docker Desktop was not found in /Applications or ~/Applications. Install/open Docker Desktop (https://docs.docker.com/desktop/setup/install/mac-install/), or start your alternative Docker runtime and select its context. Confirm docker info succeeds, then rerun.'
  else
    fail 'Docker is unavailable to your user. On systemd hosts run sudo systemctl start docker; after Docker group changes log out and back in. Check docker info and docker context ls.'
  fi
fi
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || fail 'Docker must run Linux containers.'
command -v jq >/dev/null 2>&1 || fail 'Install jq to inspect existing Minikube configuration (init installs it when missing).'
docker_memory="$(docker info --format '{{.MemTotal}}')"
docker_cpus="$(docker info --format '{{.NCPU}}')"
[[ "$docker_memory" =~ ^[0-9]+$ && "$docker_cpus" =~ ^[0-9]+$ ]] || fail 'Could not read Docker CPU/RAM capacity.'
host_mib=$((docker_memory / 1024 / 1024))
budget=$((host_mib - 1024))
if [[ "$MEMORY" == auto ]]; then
  MEMORY=$((budget / NODES / 256 * 256))
  (( MEMORY <= 2560 )) || MEMORY=2560
fi
(( MEMORY >= 2048 && NODES * MEMORY <= budget )) || fail "Insufficient physical RAM: Docker has $host_mib MiB; reserve 1024 MiB for the host. Requested $NODES nodes x $MEMORY MiB. Swap is not counted."
(( CPUS >= 2 && CPUS <= docker_cpus )) || fail "CPUS must be between 2 and Docker's $docker_cpus CPUs."
echo "Docker capacity: $host_mib MiB RAM, $docker_cpus CPUs. Selected: $NODES node(s), $MEMORY MiB and $CPUS CPUs per node."
profile_config() { printf '%s/profiles/%s/config.json' "${MINIKUBE_HOME:-$HOME/.minikube}" "$1"; }
config_matches() {
  jq -e --argjson nodes "$NODES" --argjson memory "$MEMORY" --argjson cpus "$CPUS" \
    '.Driver == "docker" and (.Nodes | length) == $nodes and .Memory == $memory and .CPUs == $cpus' "$1" >/dev/null
}
[[ "$API_PORT" =~ ^[1-9][0-9]{3,4}$ ]] && (( API_PORT >= 1024 && API_PORT <= 65535 )) || fail 'API_PORT must be between 1024 and 65535.'
NETWORK_ID=local
[[ -z "$PUBLIC_HOST" || -z "$PUBLIC_IP" ]] || fail 'Set PUBLIC_HOST or PUBLIC_IP, not both.'
if [[ -n "$PUBLIC_HOST" ]]; then
  PUBLIC_HOST="$(printf '%s' "$PUBLIC_HOST" | tr '[:upper:]' '[:lower:]')"
  API_HOST="$PUBLIC_HOST"
  jq -en --arg host "$PUBLIC_HOST" '$host | (length <= 253) and (contains(".")) and (split(".") | all(.[]; length >= 1 and length <= 63 and test("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"))) and (test("^[0-9.]+$") | not)' >/dev/null || fail 'PUBLIC_HOST must be a DNS hostname, e.g. jithendar.com, without https://, a port, or a path.'
  NETWORK_ARGS=(--listen-address=0.0.0.0 "--apiserver-names=$PUBLIC_HOST")
  NETWORK_ID="dns-$PUBLIC_HOST"
elif [[ -n "$PUBLIC_IP" ]]; then
  [[ "$PUBLIC_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'PUBLIC_IP must be an IPv4 address.'
  jq -en --arg ip "$PUBLIC_IP" '$ip | split(".") | all(.[]; (tonumber >= 0 and tonumber <= 255))' >/dev/null || fail 'Invalid PUBLIC_IP.'
  NETWORK_ARGS=(--listen-address=0.0.0.0 "--apiserver-ips=$PUBLIC_IP")
  NETWORK_ID="direct-${PUBLIC_IP//./-}"
fi
network_matches() {
  local saved=local
  if [[ -f "$STATE_DIR/$1.network" ]]; then saved="$(cat "$STATE_DIR/$1.network")"; fi
  [[ "$saved" == "$NETWORK_ID" ]]
}
# Existing Docker port bindings cannot be changed in place. Preserve populated
# clusters and fail before host changes rather than silently choosing another port.
if [[ -n "$API_HOST" ]]; then
  existing_bindings="$(docker port "$PROFILE" 8443/tcp 2>/dev/null || true)"
  if [[ -n "$existing_bindings" ]] && ! printf '%s\n' "$existing_bindings" | grep -Fxq "0.0.0.0:$API_PORT"; then
    fail "Existing profile $PROFILE does not publish API_PORT=$API_PORT. No cluster changed. Use its current port or explicitly recreate the cluster after backing up data."
  fi
  NETWORK_ARGS+=("--ports=0.0.0.0:$API_PORT:8443")
fi
# Fix host limits before stopping/replacing or starting any cluster.
if [[ "$OS" == linux ]]; then
  bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/host-limits.sh"
fi
existing_config="$(profile_config "$PROFILE")"
if [[ -f "$existing_config" ]]; then
  jq -e '.Driver and .Memory and .CPUs and (.Nodes | type == "array")' "$existing_config" >/dev/null || fail 'Cannot parse existing profile configuration; no cluster changed.'
  echo "Existing profile $PROFILE: $(jq -c '{Driver, CPUs, Memory, nodes: (.Nodes | length)}' "$existing_config")"
  if ! config_matches "$existing_config" || ! network_matches "$PROFILE"; then
    previous_profile="$PROFILE"
    PROFILE="$BASE_PROFILE-fit-${NODES}n-${MEMORY}m-${CPUS}c"
    [[ "$NETWORK_ID" == local ]] || PROFILE="$PROFILE-$NETWORK_ID"
    target_config="$(profile_config "$PROFILE")"
    if [[ -f "$target_config" ]] && { ! config_matches "$target_config" || ! network_matches "$PROFILE"; }; then
      fail "Replacement profile $PROFILE already exists with different settings; no cluster changed."
    fi
    [[ "$previous_profile" != "$PROFILE" ]] || fail 'Existing adjusted profile has incompatible settings.'
    echo "Stopping and preserving $previous_profile; using $PROFILE. Existing workloads/data remain in $previous_profile and are not migrated."
    minikube --profile="$previous_profile" stop
  fi
fi
echo "Starting $PROFILE: $NODES nodes, each with $CPUS CPUs and $MEMORY MiB RAM."
mkdir -p "$STATE_DIR"
printf '%s\n' "$NETWORK_ID" > "$STATE_DIR/$PROFILE.network"
if minikube start ${ROOT_ARGS[@]+"${ROOT_ARGS[@]}"} ${NETWORK_ARGS[@]+"${NETWORK_ARGS[@]}"} --profile="$PROFILE" --driver=docker --nodes="$NODES" \
  --cpus="$CPUS" --memory="$MEMORY" --container-runtime=containerd --wait=all --wait-timeout=10m; then
  :
else
  start_status=$?
  echo 'Startup failed; collecting diagnostics. The existing cluster is preserved.' >&2
  diagnose
  exit "$start_status"
fi
kubectl --context="$PROFILE" wait --for=condition=Ready nodes --all --timeout=300s
count="$(kubectl --context="$PROFILE" get nodes -o name | wc -l | tr -d '[:space:]')"
[[ "$count" == "$NODES" ]] || fail "Expected $NODES nodes, found $count. Use a fresh PROFILE when changing topology."
kubectl --context="$PROFILE" get nodes -o wide
mkdir -p "$STATE_DIR"
echo "Ready. Use: kubectl --context=$PROFILE get pods -A"

if [[ -n "$API_HOST" ]]; then
  published_port="$(docker port "$PROFILE" 8443/tcp | awk -F: -v wanted="$API_PORT" '$0 == "0.0.0.0:" wanted {print $NF; exit}')"
  [[ "$published_port" =~ ^[0-9]+$ ]] || fail 'Could not find the public Docker API port; kubeconfig was not exported.'
  mkdir -p "$HOME/.kube"
  export_tmp="$(mktemp "$HOME/.kube/.direct.XXXXXX")"
  chmod 600 "$export_tmp"
  if kubectl --context="$PROFILE" config view --minify --raw --flatten -o json | \
    jq --arg endpoint "https://$API_HOST:$published_port" \
      '.clusters[0].cluster.server = $endpoint | .clusters[0].cluster["insecure-skip-tls-verify"] = false | del(.clusters[0].cluster["tls-server-name"], .clusters[0].cluster["proxy-url"])' > "$export_tmp"; then
    mv "$export_tmp" "$HOME/.kube/$PROFILE-direct.json"
  else
    rm -f "$export_tmp"
    fail 'Could not export direct kubeconfig.'
  fi
  echo "Direct API: https://$API_HOST:$published_port"
  echo "In Vultr Firewall, allow TCP $published_port from your local public IP."
  echo "On your local machine:"
  printf '  scp %s@%s:%s/.kube/%s-direct.json ./vultr-kubeconfig.json\n' "$(id -un)" "$API_HOST" "$HOME" "$PROFILE"
  echo '  chmod 600 ./vultr-kubeconfig.json'
  echo '  KUBECONFIG="$PWD/vultr-kubeconfig.json" kubectl get nodes'
fi

# Commit the selected profile only after all requested access checks/export succeed.
state_tmp="$(mktemp "$STATE_DIR/.profile.XXXXXX")"
printf '%s\n' "$PROFILE" > "$state_tmp"
mv "$state_tmp" "$STATE_FILE"
