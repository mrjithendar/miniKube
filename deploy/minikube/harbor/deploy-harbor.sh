#!/usr/bin/env bash
# Build Harbor's images for arm64, push them to a private Docker Hub repository, and deploy Harbor on mk4 with
# Terraform (minikube-infrastructure/harbor.tf). README.md lists what must exist first (DNS, Docker Hub tokens).
#
#   1. build  Harbor's own Makefile on the pinned tag, on a native arm64 Docker host that is not the mk4 server
#             (image builds there froze the cluster twice: runner/README.md in the tools repository). An Apple Silicon Mac works, and
#             .github/workflows/build-harbor-images.yml runs this step on GitHub's arm64 runner.
#   2. push   docker push jithendar/custom-harbour:<component>-v2.15.4 (run "docker login" first)
#   3. deploy create-secrets.sh (harbor-credentials), Secret dockerhub-pull, then terraform apply (asks to confirm)
#
# Usage: bash deploy/minikube/harbor/deploy-harbor.sh [--skip-build] [--skip-deploy] [--with-trivy]
# Env:   IMAGE_REPO (jithendar/custom-harbour), KUBE_CONTEXT (mk4),
#        DOCKERHUB_USERNAME / DOCKERHUB_TOKEN for the pull Secret (prompted if unset; a read-only access token is enough)
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
HARBOR_VERSION=v2.15.4
HARBOR_COMMIT=0a6bf30d54e779962ad5b862fc67e3e9f6b010d8   # git rev-parse v2.15.4^{commit}
TRIVY_VERSION=v0.75.0                                     # TRIVYVERSION in that tag's Makefile
IMAGE_REPO="${IMAGE_REPO:-jithendar/custom-harbour}"     # values.yaml: docker.io/jithendar/custom-harbour
KUBE_CONTEXT="${KUBE_CONTEXT:-mk4}"
PULL_SECRET=dockerhub-pull                                # values.yaml: imagePullSecrets
BUILD=true DEPLOY=true WITH_TRIVY=false

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }
for arg in "$@"; do
  case "$arg" in
    --skip-build) BUILD=false ;;
    --skip-deploy) DEPLOY=false ;;
    --with-trivy) WITH_TRIVY=true ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fail "unknown argument: $arg (see --help)" ;;
  esac
done

# What values.yaml deploys. db, valkey, nginx and the exporter are not used (README.md); the exporter cannot
# build for arm64 at this tag (make/photon/exporter/Dockerfile sets GOARCH=amd64).
TARGETS="_build_portal _build_core _build_jobservice _build_registry _build_registryctl"
IMAGES="harbor-portal harbor-core harbor-jobservice registry-photon harbor-registryctl"
if $WITH_TRIVY; then
  TARGETS="$TARGETS _build_trivy_adapter"
  IMAGES="$IMAGES trivy-adapter-photon"
fi

build_and_push() {
  for command in docker git make; do
    command -v "$command" >/dev/null || fail "$command is required to build"
  done
  docker compose version >/dev/null 2>&1 || fail 'the Docker Compose plugin is required (make/checkenv.sh checks for it)'
  [ "$(docker version --format '{{.Server.Arch}}')" = arm64 ] || fail 'the Docker server must be arm64 (native, not emulated)'
  if docker ps --format '{{.Names}}' | grep -qx mk4; then
    fail 'this is the mk4 host: build elsewhere (see runner/README.md in the tools repository), or pass --skip-build'
  fi

  step "Building Harbor $HARBOR_VERSION for arm64"
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$HARBOR_VERSION" https://github.com/goharbor/harbor.git "$WORK/harbor"
  [ "$(git -C "$WORK/harbor" rev-parse HEAD)" = "$HARBOR_COMMIT" ] || fail "$HARBOR_VERSION does not point at $HARBOR_COMMIT any more"
  # Harbor's base images start FROM goharbor/photon:5.0-legacy, which Docker Hub has for amd64 only. Build it
  # here the way Harbor's release workflow does (.github/workflows/build-package.yml); with
  # PULL_BASE_FROM_DOCKERHUB=false no later step pulls the amd64 copy over it.
  (cd "$WORK/harbor" &&
    docker build --quiet -f make/photon/common/Dockerfile -t goharbor/photon:5.0-legacy . &&
    make compile build \
      VERSIONTAG="$HARBOR_VERSION" BASEIMAGETAG="$HARBOR_VERSION" PKGVERSIONTAG="$HARBOR_VERSION" \
      BUILD_BASE=true PULL_BASE_FROM_DOCKERHUB=false PUSHBASEIMAGE=false \
      BUILDREG=true BUILD_INSTALLER=false EXPORTERFLAG=false \
      TRIVYFLAG="$WITH_TRIVY" BUILDTRIVYADP=true \
      TRIVY_DOWNLOAD_URL="https://github.com/aquasecurity/trivy/releases/download/$TRIVY_VERSION/trivy_${TRIVY_VERSION#v}_Linux-ARM64.tar.gz" \
      GOBUILDTAGS="include_oss include_gcs" \
      BUILDTARGET="$TARGETS")

  step "Pushing to $IMAGE_REPO"
  for image in $IMAGES; do
    arch="$(docker image inspect --format '{{.Architecture}}' "goharbor/$image:$HARBOR_VERSION")"
    [ "$arch" = arm64 ] || fail "goharbor/$image:$HARBOR_VERSION is $arch, not arm64"
    docker tag "goharbor/$image:$HARBOR_VERSION" "$IMAGE_REPO:$image-$HARBOR_VERSION"
    docker push --quiet "$IMAGE_REPO:$image-$HARBOR_VERSION" ||
      fail "push failed: run 'docker login' as an account that can push to $IMAGE_REPO"
  done
}

ensure_pull_secret() {
  if [ -n "$(kubectl --context "$KUBE_CONTEXT" -n harbor get secret "$PULL_SECRET" --ignore-not-found -o name)" ]; then
    echo "Reusing harbor/$PULL_SECRET (delete it to replace the token)."
    return
  fi
  local user="${DOCKERHUB_USERNAME:-}" token="${DOCKERHUB_TOKEN:-}"
  [ -n "$user" ] || read -rp 'Docker Hub user for image pulls: ' user
  [ -n "$token" ] || { read -rsp "Docker Hub access token for $user (read-only is enough): " token; echo; }
  [ -n "$user" ] && [ -n "$token" ] || fail 'a Docker Hub user and access token are required for the pull Secret'
  # The token reaches kubectl on stdin, never a command line.
  DOCKERHUB_USERNAME="$user" DOCKERHUB_TOKEN="$token" python3 -c '
import base64, json, os
auth = base64.b64encode((os.environ["DOCKERHUB_USERNAME"] + ":" + os.environ["DOCKERHUB_TOKEN"]).encode()).decode()
config = json.dumps({"auths": {"https://index.docker.io/v1/": {"auth": auth}}})
print(json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/dockerconfigjson",
                  "metadata": {"name": "'"$PULL_SECRET"'", "namespace": "harbor", "labels": {"app.kubernetes.io/part-of": "harbor"}},
                  "data": {".dockerconfigjson": base64.b64encode(config.encode()).decode()}}))
' | kubectl --context "$KUBE_CONTEXT" apply -f - >/dev/null
  echo "Created harbor/$PULL_SECRET."
}

deploy() {
  for command in kubectl python3 terraform; do
    command -v "$command" >/dev/null || fail "$command is required to deploy"
  done
  if command -v docker >/dev/null; then
    for image in $IMAGES; do
      docker manifest inspect "$IMAGE_REPO:$image-$HARBOR_VERSION" >/dev/null 2>&1 ||
        fail "$IMAGE_REPO:$image-$HARBOR_VERSION not found (or not visible to your docker login): build it first"
    done
  fi

  step "Credentials"
  "$ROOT/create-secrets.sh" --context "$KUBE_CONTEXT" --services harbor --apply
  ensure_pull_secret

  step "Terraform: releases harbor-access and harbor (minikube-infrastructure/harbor.tf)"
  # The root also pulls OCI charts (Valkey, CloudNativePG); an empty Docker config keeps a docker credsStore helper
  # that is not installed from breaking those pulls (minikube-infrastructure/README.md).
  DOCKER_CONFIG="$(mktemp -d)" terraform -chdir="$ROOT/minikube-infrastructure" apply -var-file=vars/mk4.tfvars
  kubectl --context "$KUBE_CONTEXT" -n harbor get pods,pvc,certificate,virtualservice
  echo
  echo 'Admin password: kubectl --context '"$KUBE_CONTEXT"' -n harbor get secret harbor-credentials -o jsonpath='"'"'{.data.HARBOR_ADMIN_PASSWORD}'"'"' | base64 -d'
  echo 'Back up harbor-credentials off-host now (README.md "Backup and restore").'
}

if $BUILD; then build_and_push; fi
if $DEPLOY; then deploy; fi
echo; echo 'Done.'
