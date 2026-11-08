#!/usr/bin/env bash
# Validate the supported host before operational side effects.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/lib/require-centos.sh"
roboshop_require_centos "${1:-}" || exit 1
# Build one image locally, then publish it to JFrog's docker-local repository.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: bash deploy/minikube/jfrog/publish-image.sh SERVICE' >&2; exit 2; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
service="$1"
# Validate against the build script's allowlist before prompting for credentials.
bash "$ROOT/build-images.sh" --service "$service" --dry-run >/dev/null
read -r -p 'JFrog username (use a CI/deployer account): ' username
read -r -s -p 'JFrog access token: ' token
printf '\n'
[[ -n "$username" && -n "$token" ]] || { echo 'Username and token are required.' >&2; exit 1; }
config="$(mktemp -d)"
chmod 700 "$config"
trap 'rm -rf "$config"' EXIT
printf '%s' "$token" | docker --config "$config" login jfrog.jithendar.com --username "$username" --password-stdin
unset token
bash "$ROOT/build-images.sh" --service "$service" --local
suffix="$service"
[[ "$service" != shipping-service ]] || suffix=shipping
source="${IMAGE_PREFIX:-roboshop}-$suffix:local"
target="jfrog.jithendar.com/docker-local/roboshop-$suffix:local"
docker tag "$source" "$target"
docker --config "$config" push "$target"
printf 'Published %s\n' "$target"
