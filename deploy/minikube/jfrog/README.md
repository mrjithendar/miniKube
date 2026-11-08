> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. Use the Argo CD catalog and the five supported commands
> documented in `docs/operations/scripts.md`. Historical examples below are not
> runnable deployment instructions.

# JFrog Container Registry in Minikube

This installs the free self-managed **JFrog Container Registry (JCR)** edition of
Artifactory, pinned to Helm chart `107.161.26`. It supports Docker/OCI images, Helm
and Generic file artifacts. Managed Maven, npm and PyPI repositories are not
included. Docker builds images; JFrog stores the results.

- Namespace/release: `jfrog`.
- UI: **https://jfrog.jithendar.com/ui/**.
- Shortcut: **https://jithendar.com/jfrog** redirects to the UI.
- Docker registry host: **jfrog.jithendar.com** (no `/jfrog` login suffix).
- Persistence: 30 GiB artifact PVC plus 10 GiB PostgreSQL PVC, using the default
  storage class. Minikube local storage is development storage, not HA storage.
- TLS: one Certificate `jfrog/jfrog-public`, Secret `jfrog-public-tls`, covering
  both hosts. Existing WordPress/monitoring/RoboShop certificates are untouched.
- The chart's NGINX is private ClusterIP HTTP behind Traefik's public HTTPS.

## Prerequisites

The standard profile targets more capacity than the existing **4-vCPU/8-GB host**.
For a best-effort experiment on that host, see the minimal profile below.
Use an upgraded host (recommend at least 8 vCPU/16 GB for this sandbox) with enough
free resources and disk, or a dedicated JFrog host. The standard profile requires at least
one schedulable node with 6 allocatable CPUs and 12 GiB RAM. This is a baseline
check, not a guarantee of free capacity: inspect existing requests and usage too.
Three Minikube containers on one host do not provide three independent RAM pools.
Changing the Vultr plan alone might not change Minikube's container allocation;
check `kubectl describe nodes`. Do not wipe existing database volumes to resize.

Create a DNS **A record `jfrog.jithendar.com` → your Vultr public IPv4 address**.
If you publish AAAA, it must route to this same ingress over IPv6. Keep
`jithendar.com` pointing to the same ingress. Public ports 80/443, Traefik,
cert-manager, `letsencrypt-prod`, Helm, kubectl and Python 3 are required.
No paid JFrog license or external database account is needed.

## Install

```bash
# Read-only capacity/DNS/ingress checks; creates nothing.
bash deploy/minikube/deploy-jfrog.sh --check

# Optional: render the pinned chart locally without contacting Kubernetes.
bash deploy/minikube/deploy-jfrog.sh --render-only > /tmp/jfrog.yaml

# Creates namespace/credentials, installs Helm, then ingress and certificate.
bash deploy/minikube/deploy-jfrog.sh
```

The script resolves `PROFILE=mk4` including installer replacement profiles.
Override `KUBE_CONTEXT` or `KUBECONFIG` explicitly if needed. Existing
`$HOME/.kube/vultr-minikube.json` is used when KUBECONFIG is unset.

On rerun credentials are reused, not rotated. Missing credentials with existing
PVCs cause an error rather than losing access to the stored artifacts. Back up the
`jfrog-bootstrap` Secret alongside database/filestore backups: the master key is
required to recover encrypted data. The installer never deletes PVCs.

## First login and repositories

Username: **admin**. Retrieve your generated password privately (not in CI logs):

```bash
kubectl -n jfrog get secret jfrog-bootstrap \
  -o jsonpath='{.data.admin-password}' | base64 --decode; echo
```

This is a random password, not JFrog's default `password`. Open the UI and complete
the onboarding/EULA yourself. No commercial-license activation is required. Create:

| Repository key | Package type | Use |
| --- | --- | --- |
| `docker-local` | Docker, local | Application container images |
| `generic-local` | Generic, local | ZIPs, JAR files, reports and other build outputs |
| `helm-local` | Helm OCI, local | Packaged Helm charts |

Use the Docker **repository path** access method. Set the custom base URL to
`https://jfrog.jithendar.com` if prompted. Keep anonymous access disabled. Create a
separate deployer account/token with read/deploy rights only on these repositories;
use a read-only account/token for Kubernetes image pulls. Follow each repository's
**Set Me Up** instructions for Docker/Helm upload commands. Generic files can be
uploaded directly through the UI or its authenticated artifact-upload API.

## Build and publish a Docker image

After TLS, onboarding and `docker-local` are ready:

```bash
bash deploy/minikube/jfrog/publish-image.sh frontend
# or: shipping-service, ai-gateway, moks, etc.
```

The helper prompts privately for the access token, builds via the existing root
script, tags the result and pushes, for example:
`jfrog.jithendar.com/docker-local/roboshop-frontend:local`.
Its temporary Docker credentials are removed on exit; existing Docker login files
are untouched. It does not deploy or alter the existing local-image workflow.
For repeatable releases, publish immutable version/commit tags when migrating CI.
The current `:local` tag deliberately remains mutable to match the project scripts.

Kubernetes must have a registry pull Secret before switching workloads to JFrog
images. The existing local-image deployments do not need that Secret and remain
unchanged. Registry-based CI/deployment can be connected after the registry is
running and deployer/read-only tokens have been created; do not use the admin token
for application image pulls.

## Troubleshooting

```bash
kubectl -n jfrog get pods,pvc,svc,ingress,certificate
kubectl -n jfrog describe pods
kubectl -n jfrog logs statefulset/jfrog-artifactory -c artifactory --tail=100
kubectl -n jfrog describe certificate jfrog-public
kubectl -n jfrog get certificaterequest,order,challenge
```

- **Capacity preflight fails:** upgrade host/node allocation, or explicitly select
  the experimental minimal profile for sandbox evaluation.
- **Pending pods:** inspect scheduler events, CPU/memory requests and storage class.
- **Certificate pending:** check DNS for BOTH names and Let's Encrypt HTTP-01 port 80.
- **Docker login/push fails:** use the hostname, valid TLS and `docker-local` path;
  verify repository type and token permissions. Do not disable TLS validation.
- **413 / upload timeout:** inspect Traefik request size/timeouts and chart NGINX.
- **Wrong bootstrap credentials:** restore the original Secret; changing encryption
  or PostgreSQL keys on an existing installation is not a password reset.

Sources: [JCR setup](https://docs.jfrog.com/artifactory/docs/get-started-jfrog-container-registry),
[system requirements](https://docs.jfrog.com/installation/docs/general-system-requirements),
[official Helm chart](https://github.com/jfrog/charts/tree/master/stable/artifactory-jcr).

## Experimental minimal sandbox profile

```bash
JFROG_SIZE=minimal bash deploy/minikube/deploy-jfrog.sh --check
JFROG_SIZE=minimal bash deploy/minikube/deploy-jfrog.sh
```

This overlays `values-minimal.yaml` onto the standard configuration. It keeps
PostgreSQL, persistent data, generated credentials, TLS and all required JFrog
services. The pinned 7.161 release no longer supports single-container mode.
Artifactory uses a 512 MiB initial / 1 GiB maximum Java heap and a 2 GiB container
limit. Access/config JVMs and every other running container have explicit limits.
The rendered regular containers request **2,752 MiB total** and have **5,504 MiB
combined limits**, excluding init containers and Kubernetes/host overhead.
These numbers are limits/requests, not measured consumption or a stability promise.

The minimal capacity gate accepts a node with 2 CPUs / 4 GiB allocatable memory;
it does not claim that memory is currently free. Your other applications still
consume memory on the same host. Expect slow startup; OOMKilled, restart loops or
host memory pressure mean the profile is too small for your workload. Reduce
other workloads or upgrade the host rather than disabling memory limits. Avoid
concurrent image builds and large uploads on a busy 8 GB host. This profile is
below the vendor sizing requirements and is not suitable for production.

Both profiles preserve PVC sizes and existing credentials. Select `JFROG_SIZE`
on every install/upgrade; omitting it uses the larger standard profile. Use
`JFROG_SIZE=minimal bash deploy/minikube/deploy-jfrog.sh --render-only` to inspect the
complete manifests. DNS and certificate prerequisites still apply in minimal mode.
