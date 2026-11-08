# miniKube

Everything for the self-hosted Minikube cluster `mk4`: creating the cluster, Traefik and Argo CD, the Terraform-managed
add-ons (cert-manager, Let's Encrypt, Istio, Kiali, Prometheus, Grafana, Loki, OpenTelemetry Collector, Jaeger), the
mk4 Argo CD catalog (AppProject `mk-platform`, opt-in Harbor), and their workflows, scripts and tests.

Moved here from [mrjithendar/tools](https://github.com/mrjithendar/tools) on 2026-11-08 with the same paths, so
`deploy/minikube/...`, `minikube-infrastructure/...` and `scripts/argocd/...` mean what they meant there. tools keeps
the AWS account (`infrastructure/`), EKS (`deploy/eks/`), the labauto installers and `init.sh`.

## Quick start

```bash
../tools/init.sh --profile minikube   # host prerequisites: Docker, kubectl, Helm, Minikube (tools repository)
./deploy-minikube.sh                  # create the cluster
./bootstrap-cluster.sh                # Traefik, Argo CD, ECR pull secrets
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars   # add-ons (minikube-infrastructure/README.md)
./create-secrets.sh --apply           # missing installation credentials
./deploy-argocd.sh roboshop           # register and sync the RoboShop applications
```

Every entry point can run from any directory, and most accept `--help`.

| Command | Does |
|---|---|
| `./deploy-minikube.sh` | Create or update the Minikube cluster (`deploy/minikube/install.sh`). `install` hands off to `../tools/init.sh` (or `TOOLS_DIR`). |
| `./bootstrap-cluster.sh` | Install Traefik with Helm, then Argo CD and its ingress; refresh ECR pull secrets. `--schedule-pull-secrets` adds a six-hourly refresh CronJob ([details](deploy/minikube/ecr-refresh/README.md)). |
| `./create-secrets.sh [--apply] [--all]` | Create missing installation credentials. Never rotates existing ones. |
| `./deploy-argocd.sh [app\|all\|roboshop\|list\|status]` | Register and sync Argo CD Applications. |
| `./deploy-traefik.sh` | Install or upgrade Traefik on its own (`deploy/minikube/deploy-traefik.sh`). |
| `./harbor-pull-secret.sh [context]` | Create or rotate Secret `harbor-pull` (registry.jithendar.com) in every active namespace. |
| `scripts/setup-minikube-terraform-ci.sh` | One-time access for the Terraform workflow ([minikube-infrastructure/README.md](minikube-infrastructure/README.md#ci)). |
| `scripts/data/seed-minikube-demo.py` | Opt-in synthetic demo data. |
| `scripts/reset-mongo-password.sh` | Reset the RoboShop MongoDB password on mk4. |

Less common operations in `deploy/minikube/`:

| Script | Does |
|---|---|
| `kubeconfig.sh` | Copy the remote cluster's kubeconfig over SSH and validate it. |
| `tunnel-endpoint.sh` | SSH-forward the Kubernetes API when it is not directly reachable. |
| `expose-ingress.sh` | Forward host ports 80/443 to the Minikube ingress. |
| `host-limits.sh` | Raise Linux inotify limits (run automatically by `install.sh`). |
| `deploy-vault.sh` | Install persistent Vault with Helm ([details](deploy/minikube/vault/README.md)). |
| `harbor/deploy-harbor.sh` | Build Harbor's arm64 images and deploy Harbor ([details](deploy/minikube/harbor/README.md)). |
| `reset.sh` | **Destructive**: delete the Minikube clusters and installer state. Asks for confirmation. |

## Layout

| Path | Contents |
|---|---|
| `deploy/minikube/` | Cluster scripts and per-component config: [argocd](deploy/minikube/argocd/README.md), [cert-manager](deploy/minikube/cert-manager/README.md), [traefik](deploy/minikube/traefik/README.md), [vault](deploy/minikube/vault/README.md), [jfrog](deploy/minikube/jfrog/README.md), [harbor](deploy/minikube/harbor/README.md), [metrics-server](deploy/minikube/metrics-server/README.md), [ecr-refresh](deploy/minikube/ecr-refresh/README.md), `namespaces/`, the [Argo CD catalog](deploy/minikube/applications/README.md) and [observability](deploy/minikube/observability/README.md). [Commands](deploy/minikube/README.md). |
| `minikube-infrastructure/` | Terraform root for the add-ons, one `helm_release` per file ([details](minikube-infrastructure/README.md)). |
| `scripts/argocd/` | `bootstrap.py`, `deploy.py`, and `validate.py` (renders the whole catalog and checks ownership). |
| `scripts/setup/` | `create_secrets.py` and [SECRETS.md](scripts/setup/SECRETS.md). |
| `scripts/lib/` | Shared helpers: supported-host check, Argo CD ownership check, repository paths. |
| `.github/workflows/` | `check-mk-infrastructure.yml` (PRs), `create-mk-infrastructure.yml` (Terraform apply on master), `build-harbor-images.yml` (manual). |
| `tests/`, `deploy/minikube/test-*.py` | Tests for everything above. |
| `docs/centos7-manual-setup.md`, `dashboard-admin.yaml` | This repository's original CentOS 7 notes and dashboard ServiceAccount, kept for reference. |

## Other repositories

Clone them side by side, or set `TOOLS_DIR`, `ROBOSHOP_AI_DIR` and `JITHENDAR_COM_DIR`:

```text
Work/
  miniKube/      this repository
  tools/         init.sh (host prerequisites), AWS Terraform (incl. this CI's OIDC role), EKS
  roboshop-ai/   the shop's Argo CD catalog, microservices and manifests
  jithendar.com/ shared certificate, default Traefik TLSStore, Grafana dashboards (read by grafana.tf)
```

Argo CD deploys the **pushed** revision of `master`. Commit and push changes here before running `./deploy-argocd.sh`.

## Moving from tools (one time)

Paths inside the repository did not change; the repository did. Push this repository **before** the tools change that
deletes these files, then:

1. **Argo CD access.** This repository is private, so Argo CD on mk4 needs a repository credential for
   `https://github.com/mrjithendar/miniKube.git` (a read-only fine-grained token or deploy key), e.g.
   `argocd repo add https://github.com/mrjithendar/miniKube.git --username x-access-token --password <token>`.
2. **Re-apply the live specs** that read this repository (only Harbor and the AppProjects):
   `./deploy-argocd.sh harbor --context mk4`, then `./deploy-argocd.sh status --context mk4`.
3. **GitHub settings** (they do not move with the files): run `scripts/setup-minikube-terraform-ci.sh` to create the
   environment `mk4` and its secret and variables here; add `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` for the Harbor
   build; set `MINIKUBE_TERRAFORM_APPLY` only when tools had it. Then delete the same names from tools.
4. **OIDC role.** tools' `infrastructure/github-minikube-terraform.tf` now trusts `repo:mrjithendar/miniKube:environment:mk4`;
   it applies when that tools change reaches tools `master`.
5. **mk4 host.** Scripts run on the host (`expose-ingress.sh`, `deploy-harbor.sh --skip-build`) now come from a
   clone of this repository, not of tools.
6. **arm64 runner.** `build-harbor-images.yml` runs on `ubuntu-24.04-arm`, which was free because tools is public. On a
   private repository, check that your plan offers it and what it costs before running the workflow.

## Tests

Needs Python 3.9+ with `pytest` and `PyYAML`, Helm, and the roboshop-ai and jithendar.com checkouts.

```bash
python3 scripts/argocd/validate.py
python3 -m pytest -q tests deploy/minikube/test-*.py
```

The Vault chart test runs only against a rendered chart:

```bash
helm template vault vault --repo https://helm.releases.hashicorp.com --version 0.34.1 \
  -f deploy/minikube/vault/values.yaml > /tmp/vault.yaml
python3 tests/test_vault_chart.py /tmp/vault.yaml
```

## License

Copyright (c) 2026 Jithendar Dharmapuri. All rights reserved. This is proprietary software; no use, copying, modification or distribution is permitted without written permission.
