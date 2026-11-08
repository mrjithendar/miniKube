# minikube-infrastructure: cluster add-ons on mk4 (Terraform Helm releases)

Terraform root for the Minikube cluster `mk4`. The AWS account's Terraform (`infrastructure/`) is in the tools repository. One file per add-on, every add-on a
`helm_release`. None of these are Argo CD Applications any more; the catalog in `deploy/minikube/applications` holds
only the AppProject `mk-platform` and the opt-in `harbor`.

| File | Release | Chart | Namespace | Values / source |
|---|---|---|---|---|
| `traefik.tf` | `traefik` (only while `traefik_enabled`) | traefik `traefik` 42.0.0 (v3.7) | `traefik` | Being retired ([Leaving Traefik](../deploy/minikube/istio/README.md#leaving-traefik-once-in-this-order)); `deploy/minikube/traefik/values.yaml`; the live release is imported, not reinstalled (`adopt_existing_traefik`) |
| `metrics-server.tf` | `metrics-server` | kubernetes-sigs `metrics-server` 3.14.0 | `kube-system` | `deploy/minikube/metrics-server/values.yaml` |
| `cert-manager.tf` | `cert-manager` | `oci://quay.io/jetstack/charts/cert-manager` v1.20.4, CRDs included | `cert-manager` | `deploy/minikube/cert-manager/values.yaml` |
| `letsencrypt.tf` | `letsencrypt-jithendar` | local `deploy/minikube/cert-manager/issuers` | `cert-manager` | ClusterIssuers `letsencrypt-dns`, `letsencrypt-dns-staging` (DNS-01, Cloudflare); Certificate `jithendar/jithendar-com-wildcard` for `jithendar.com` + `*.jithendar.com` |
| `istio.tf` | `istio-base`, `istio-cni`, `istiod` | istio-release `base`, `cni`, `istiod` 1.30.7 | `istio-system` | `deploy/minikube/istio/istiod.yaml` |
| `istio.tf` | `istio-ingressgateway` | istio-release `gateway` 1.30.7 | `istio-ingress` | `deploy/minikube/istio/ingressgateway.yaml`; NodePort 31080 (HTTP) / 31443 (HTTPS), not forwarded from the host |
| `istio.tf` | `istio-edge` | local `deploy/minikube/istio/edge` | `istio-ingress` | mk4's public edge: Gateway `public`, wildcard certificate, the jithendar.com routing table, logins (from Secret `gateway-basic-auth`), read-only /opensearch, PROXY protocol, compression; Traefik hand-off while `traefik_enabled` ([README](../deploy/minikube/istio/README.md)) |
| `kiali.tf` | `kiali` | kiali `kiali-server` 2.34.0 | `istio-system` | `deploy/minikube/istio/kiali.yaml`; Secret `kiali-signing-key` |
| `prometheus.tf` | `prometheus` | local `deploy/minikube/observability/prometheus` | `monitoring` | chart `values.yaml` |
| `grafana.tf` | `grafana-dashboards`, `grafana` | local `deploy/minikube/observability/grafana/dashboards`; grafana-community `grafana` 14.0.1 | `monitoring` | `deploy/minikube/observability/grafana/values.yaml`; dashboards from `./dashboards` and `../jithendar.com/grafana` |
| `loki.tf` | `loki` | local `deploy/minikube/observability/loki` (Loki + Alloy) | `monitoring` | `loki.yaml`, `config.alloy` |
| `otel-collector.tf` | `otel-collector` | local `deploy/minikube/observability/otel-collector` | `observability` | `collector.yaml` |
| `jaeger.tf` | `jaeger` | local `deploy/minikube/observability/jaeger` | `observability` | `jaeger.yaml` |

`versions.tf` (providers, S3 backend), `variables.tf` (`kubeconfig_path` `~/.kube/config`, `kube_context` `mk4`,
`helm_timeout` 600 s, `jithendar_com_dir` `../../jithendar.com`, `adopt_existing_traefik` false; `true` in mk4.tfvars), `vars/mk4.tfvars`, `tests/` (`terraform test`
with mock providers).

The Loki, Jaeger, OpenTelemetry Collector and dashboard folders used to be Kustomize. They are local Helm charts now:
the manifests moved unchanged into `templates/`, and each config file stays at the chart root, loaded verbatim into a
ConfigMap with a fixed name (`loki-config`, `alloy-config`, `jaeger-config`, `otel-collector-config`). A
`checksum/config` pod annotation rolls the pod when the file changes, as the hashed Kustomize name did.

## Public routes

All through the Istio gateway on `jithendar.com` (routing table and logins: `deploy/minikube/istio/edge`), TLS from its wildcard certificate.

| URL | Sign-in |
|---|---|
| https://jithendar.com/grafana/ | Grafana admin from Secret `monitoring/monitoring-auth` |
| https://jithendar.com/prometheus/ | basic auth on the gateway, same Secret (`scripts/istio/gateway-basic-auth.py`) |
| https://jithendar.com/kiali/ | ServiceAccount token, view only (below) |
| https://jithendar.com/loki/api/v1/{query_range,labels,label/…,format_query} | basic auth, Secret `monitoring/loki-read-auth` |
| https://jithendar.com/loki/api/v1/push | basic auth, Secret `monitoring/loki-push-auth` |

Loki has no UI and no authentication of its own, so `/loki` stays limited to the read endpoints MOKS calls and the push
path. Query logs in Grafana (Explore → Loki). Jaeger and the collector stay cluster-internal.

**Kiali sign-in:** `kubectl --context mk4 -n istio-system create token kiali --duration=8h`, paste the token. The UI is
view only (`deployment.view_only_mode`). Its graph is empty until a namespace has sidecars:
`kubectl --context mk4 label namespace <ns> istio-injection=enabled`, then restart that namespace's Deployments.
Prometheus scrapes istiod and every sidecar (`istioMetrics` in the Prometheus chart). Namespaces with a NetworkPolicy
(moks, jithendar) must also admit `monitoring` on port 15090 before their sidecar metrics arrive.

## Wildcard certificate

`letsencrypt-dns` solves DNS-01 through Cloudflare, so it can issue `*.jithendar.com`. It needs Secret
`cert-manager/cloudflare-api-token` (key `api-token`, Zone:DNS:Edit and Zone:Read on jithendar.com) **before**
issuance; it was not on mk4 on 2026-11-08. The git-ignored `cloudflare-secrets.yaml` at the repository root holds it:
`kubectl --context mk4 apply -f cloudflare-secrets.yaml`. Without it the ClusterIssuers still register their ACME
accounts and the Certificate stays `False` until the Secret appears.

The existing `letsencrypt-prod` / `letsencrypt-staging` (HTTP-01) stay in the jithendar.com repository
(`minikube/45-jithendar-letsencrypt.yaml`) only for Traefik's own certificate, until Traefik is uninstalled. The Istio
gateway's wildcard certificate (`istio-ingress/jithendar-com`, edge chart) uses `letsencrypt-dns`.

## Commands

From the repository root, with AWS credentials for the state bucket and a kubeconfig that has the `mk4` context.
State: `s3://jithendardharmapuri/terraform/minikube-mk4.tfstate` (same bucket as the tools repository's `infrastructure/`, own key). The
jithendar.com repository must be checked out beside this one (or set `jithendar_com_dir`).

```bash
terraform -chdir=minikube-infrastructure init
terraform -chdir=minikube-infrastructure plan -var-file=vars/mk4.tfvars
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
```

Checks without a cluster: `terraform init -backend=false && terraform validate && terraform test`, and
`python3 -m pytest tests` from the repository root (renders every local chart).

Fresh cluster order: `./deploy-minikube.sh`, then this root (with `adopt_existing_traefik = false`; Traefik first,
because the Loki and Prometheus charts use its Middleware CRD), then `./bootstrap-cluster.sh` (Argo CD and its ingress).

## CI

| Workflow | When | Runner | Does |
|---|---|---|---|
| `.github/workflows/check-mk-infrastructure.yml` | pull requests touching this folder, `deploy/minikube/{cert-manager,istio,observability}`, the metrics-server values | GitHub-hosted, no secrets | fmt, validate, `terraform test`, `helm lint`/`template` of every local chart |
| `.github/workflows/create-mk-infrastructure.yml` ("Deploy minikube infrastructure") | push to `master` (same paths), or by hand (`plan_only` to see drift) | GitHub-hosted, environment `mk4` | prerequisite checks (no leftover Argo CD apps, Cloudflare Secret), fmt, validate, test, plan (in the run summary), apply, then checks all 13 releases are `deployed` and /grafana, /prometheus, /kiali answer |

Both check out `miniKube` and `jithendar.com` (public, `grafana/` only) side by side, so `jithendar_com_dir` keeps its
default. A dashboard change in jithendar.com does not trigger the deploy; run it by hand.

The deploy job runs only when repository variable `MINIKUBE_TERRAFORM_APPLY` is `true`. Its access:

- **mk4:** ServiceAccount `kube-system/github-minikube-terraform` (`ci/github-terraform-serviceaccount.yaml`), bound to
  `cluster-admin` (CRDs, ClusterRoles and webhooks need it). Its token is a secret of the environment `mk4`, which only
  `master` can deploy to, so pull request jobs never see it. API endpoint `MK4_API_SERVER` (`https://jithendar.com:32771`,
  the one jithendar.com's CI uses). Revoke: `kubectl --context mk4 -n kube-system delete secret github-minikube-terraform-token`.
- **State:** AWS role `github-tools-minikube-terraform` through GitHub OIDC, only for
  `repo:mrjithendar/miniKube:environment:mk4`, only the state object and its `.tflock`
  (tools repository: `infrastructure/github-minikube-terraform.tf`). No AWS key is stored. The role name keeps its
  old `github-tools-` prefix: renaming it would replace the role.

Turning it on (once):

1. In the tools repository, merge to `master`: its `create-aws-infrastructure.yml` applies `infrastructure/`, which
   creates the role (or, if it exists, switches its trust to `repo:mrjithendar/miniKube:environment:mk4`).
2. `scripts/setup-minikube-terraform-ci.sh`: applies the ServiceAccount, checks the rendered kubeconfig can do what the
   releases need, creates the environment `mk4` (master only), stores the token and the variables `MK4_API_SERVER`,
   `MK4_CA_DATA`, `MK4_TERRAFORM_ROLE_ARN`. Needs `gh` signed in with admin on the repository.
3. The move below, steps 1–6 (by hand, once). Step 6 can instead be the first CI run: do steps 1–5, then step 4 here.
4. `gh variable set MINIKUBE_TERRAFORM_APPLY --repo mrjithendar/miniKube --body true`, then
   `gh workflow run create-mk-infrastructure.yml --repo mrjithendar/miniKube -f plan_only=true` and read the plan in the
   run summary before the first real apply.

## Moving the live mk4 add-ons from Argo CD (once)

State on 2026-11-08, read from mk4: Argo CD Applications `metrics-server` (project `mk-platform`), `grafana`,
`prometheus`, `loki`, `otel-collector`, `jaeger` (project `mk-observability`), all Synced/Healthy, **no finalizers**,
so deleting them leaves every workload running. cert-manager has no Application and no Helm release. No Istio, no
Kiali. A plan with empty state showed 14 to add, 0 to change, 0 to destroy.

Every release whose objects already exist sets `take_ownership`: Helm adopts them and patches only what the chart
renders. `K="kubectl --context mk4"`. Keep backups outside `My Drive/Work`, which syncs to Google Drive.

| # | Action | Check | Pass |
|---|---|---|---|
| 1 | Back up the Applications: `mkdir -p ~/Backups/mk4-terraform && $K -n argocd get application metrics-server grafana prometheus loki otel-collector jaeger -o yaml > ~/Backups/mk4-terraform/applications.yaml` | `ls -l ~/Backups/mk4-terraform` | file non-empty |
| 2 | Confirm no finalizers: `$K -n argocd get application metrics-server grafana prometheus loki otel-collector jaeger -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.finalizers}{"\n"}{end}'` | output | every line ends after the name. If one shows `resources-finalizer…`, remove it first: `$K -n argocd patch application <name> --type=json -p '[{"op":"remove","path":"/metadata/finalizers"}]'` |
| 3 | Delete the Applications (workloads stay): `$K -n argocd delete application metrics-server grafana prometheus loki otel-collector jaeger` | `$K get deploy -n monitoring; $K get deploy -n observability; $K top nodes` | grafana, prometheus, loki, alloy, jaeger, otel-collector still `1/1`; node metrics print |
| 4 | Delete the emptied AppProjects and shrink `mk-platform`: `$K -n argocd delete appproject mk-observability logging` then `$K apply -f deploy/minikube/applications/project-mk-platform.yaml` | `$K -n argocd get appproject` | no `mk-observability`, no `logging`; `harbor` still in `mk-registry` |
| 5 | Cloudflare token for DNS-01: `$K apply -f cloudflare-secrets.yaml` | `$K -n cert-manager get secret cloudflare-api-token` | exists |
| 6 | `terraform -chdir=minikube-infrastructure init`, then `apply -var-file=vars/mk4.tfvars` | `helm --kube-context mk4 list -A` | 13 releases `deployed`, `traefik` at a new revision (the `argocd` release is not this root's) |
| 7 | Verify | `$K top pods -A \| head -3`; `$K -n istio-system get pods`; `$K get clusterissuer`; `$K -n jithendar get certificate jithendar-com-wildcard`; open /grafana/, /prometheus/, /kiali/ | metrics print; istiod and kiali Running; four issuers `True`; certificate `True` within a few minutes |
| 8 | Remove the ConfigMaps Kustomize left behind (hash-suffixed names, no longer referenced): `$K -n monitoring get cm \| grep -E '^(loki\|alloy)-config-'`, `$K -n observability get cm \| grep -E '^(jaeger\|otel-collector)-config-'`, then delete those names | the same commands | empty |

What changes on the first apply: Loki, Alloy, Jaeger and the collector roll once (ConfigMap renamed, checksum
annotation added); Jaeger keeps traces in memory, so they are lost. Prometheus rolls for the two Istio scrape jobs and
its ClusterRole gains pod list/watch. Grafana and metrics-server do not change. The PVCs carry
`helm.sh/resource-policy: keep`, so `terraform destroy` or a failed release never deletes their data.

If step 6 fails with `invalid ownership metadata`, an object still belongs to another release; with `cannot re-use a
name`, a release of that name exists (`helm --kube-context mk4 list -A`). If it fails part-way, run it again; Helm
adopts what it has not yet adopted. Undo step 3: `$K apply -f ~/Backups/mk4-terraform/applications.yaml` (Argo CD
then shows the Helm-adopted objects as its own again; do not sync with prune).

Objects keep Argo CD's `argocd.argoproj.io/tracking-id` annotation from the old Applications. No Application claims
them any more, so Argo CD ignores it.
