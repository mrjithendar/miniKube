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
| `local-path.tf` | `local-path-provisioner` | vendored `deploy/minikube/storage/local-path-provisioner` (rancher v0.0.37) | `local-path-storage` | `deploy/minikube/storage/local-path.yaml`; StorageClass `local-path` (node-pinned, Retain, not default) |
| `postgres.tf` | `cnpg` | cnpg `cloudnative-pg` 0.29.1 (operator 1.30.1) | `cnpg-system` | `deploy/minikube/postgres/operator.yaml` |
| `postgres.tf` | `postgres-cluster`, `postgres-access` | cnpg `cluster` 0.9.0; local `deploy/minikube/postgres/access` | `postgres-db` | `deploy/minikube/postgres/cluster.yaml` + per-application databases/roles/Secrets from `postgres_databases`; pgAdmin 4 (+ admin Secret `postgres-ui-admin`), TCP route, gateway allowlists (`postgres_allowed_ips`), NetworkPolicies, metrics Service |
| `argocd.tf` | `argocd` | argo `argo-cd` 10.10.5 (Argo CD v3.5.5) | `argocd` | `deploy/minikube/argocd/values.yaml`: https://jithendar.com/argocd (VirtualService delegate of the edge routing table, sidecar NetworkPolicy); no Redis of its own, its cache is Valkey tenant `argocd` (database 7, Secret `argocd-redis-valkey`) |
| `harbor.tf` | `harbor-access`, `harbor` | local `deploy/minikube/harbor/access`; goharbor `harbor` 1.19.4 (Harbor v2.15.4, arm64 images from Docker Hub `jithendar/custom-harbour`) | `harbor` | https://registry.jithendar.com (https://jithendar.com/harbor redirects there); `deploy/minikube/harbor/values.yaml`; database on the shared PostgreSQL (`postgres_databases.registry`), Redis on Valkey (`redis_tenants.harbor`); namespace (in the mesh), generated `harbor-credentials` and `dockerhub-pull` (from `dockerhub_username`/`dockerhub_password`: CI secrets `DOCKER_USERNAME`/`DOCKER_PASSWORD`, locally `TF_VAR_dockerhub_*`) are Terraform's too ([README](../deploy/minikube/harbor/README.md)) |
| `redis.tf` | `valkey`, `redis-access` | cloudpirates `valkey` 0.25.16 (Valkey 9.1, OCI); local `deploy/minikube/redis/access` | `redis` | `deploy/minikube/redis/values.yaml` + post-renderer `postrender-haproxy.sh`; per-application ACL users and Secrets from `redis_tenants`; STRICT mTLS + enforced AuthorizationPolicy, RedisInsight (gateway login `redis-ui-auth`), TCP route, gateway allowlists (`redis_allowed_ips`), NetworkPolicies, metrics Service |
| `gatekeeper.tf` | `gatekeeper`, `gatekeeper-library`, `gatekeeper-constraints` | open-policy-agent `gatekeeper` 3.23.1; local `deploy/minikube/gatekeeper/library` and `constraints` | `gatekeeper-system` | `deploy/minikube/gatekeeper/values.yaml`: validating webhook (fails open) and audit; ConstraintTemplates vendored from gatekeeper-library; deny privileged, host namespaces, host network/ports, hostPath; warn on `:latest` and missing memory limits ([README](../deploy/minikube/gatekeeper/README.md)) |

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

## PostgreSQL (CloudNativePG)

**One PostgreSQL for every application on mk4.** Cluster `postgres-cluster` in namespace `postgres-db`: PostgreSQL 18,
one primary and two streaming replicas, each on its own node with a 10Gi `local-path` volume pinned to that node, and
PgBouncer (2 pods, session mode) in front of the primary. The operator promotes a replica if the primary's pod or node
fails; PgBouncer's address stays the same. The applications' own PostgreSQL pods are to be retired onto it, one at a
time (below).

**Tenancy** (`postgres.tf`, `postgres_databases` in `vars/mk4.tfvars`): each entry `<name> = { namespace = "<ns>" }` gets

- database `<name>` owned by login role `<name>` (no other application's role can connect to it or read it), at most
  `connection_limit` connections (default 50; the server allows 300);
- a random password, kept in the encrypted Terraform state, in Secret `postgres-db/db-<name>` (the operator reads it);
- Secret `<ns>/postgres-db-credentials` with `host`, `port`, `dbname`, `username`, `password`, `uri` (all through
  PgBouncer, `postgres-cluster-pooler-rw.postgres-db.svc:5432`) and `direct-host`, `direct-uri` (the primary itself).

Removing an entry drops the role's Secrets and login; the database stays (`databaseReclaimPolicy: retain`) until
dropped by hand. Listed today: `jithendar`, `moks`, `registry` (Harbor), `shipping` (RoboShop). Their databases are
empty until each application moves.

| From | Address |
|---|---|
| applications | `uri` from `postgres-db-credentials` (PgBouncer); `-rw` (primary), `-ro` (replicas), `-r` (any) for tools |
| browser | https://jithendar.com/postgres/ (pgAdmin 4: log in with Secret `postgres-db/postgres-ui-admin`, then open server `postgres-cluster` with `postgres-cluster-superuser`'s password or an application's login) |
| psql | `psql "host=postgres.jithendar.com port=443 dbname=<name> user=<name> sslmode=verify-full sslrootcert=system sslnegotiation=direct"` (libpq 17+) |

PostgreSQL is not HTTP, so the path `/postgres` can only serve the web client; database clients use the hostname,
whose TLS the gateway terminates (wildcard certificate, ALPN `postgresql`) before forwarding TCP to the primary. Both
are refused on the gateway unless the client address is in `postgres_allowed_ips` (`vars/mk4.tfvars`); an empty list
fails the plan. Passwords: `K="kubectl --context mk4"`;
`$K -n postgres-db get secret db-<name> -o jsonpath='{.data.password}' | base64 -d`, superuser
`postgres-cluster-superuser` (user `postgres`).

**First install, in stages:** a plan of `postgres-cluster` fails with `no matches for kind "Cluster"` until the
operator's CRDs exist. Every namespace in `postgres_databases` must already exist.

```bash
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars -target=helm_release.local_path_provisioner -target=helm_release.cnpg_operator
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars -target=helm_release.postgres_cluster
$K -n postgres-db get cluster,pooler,database           # "Cluster in healthy state", 3 ready; every Database applied
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars    # edge route + TLS host, access chart, Prometheus job
```

The hostname needs a DNS record: `postgres.jithendar.com` pointing at the host like `db.jithendar.com`
(jithendar.com repository, `scripts/cloudflare-dns-record.sh`).

### Moving an application (once each)

1. **Backups first.** The cluster has none yet (`backups.enabled: false`): with every application in it, configure an
   object store (`backups` in `cluster.yaml`, the S3 bucket and a key with access to one prefix only) before step 4.
2. Its namespace's NetworkPolicy must allow egress to namespace `postgres-db` on 5432 (moks and jithendar restrict
   egress). Meshed namespaces need nothing else: `postgres-db` has no sidecars, so Istio sends plain TCP.
3. Stop writes (scale the application to 0), then copy: `pg_dump -Fc` from the old pod,
   `pg_restore --no-owner --role=<name> -d <name>` into the new database (PgBouncer or `-rw`). MOKS is on PostgreSQL 17:
   dump with the 17 client, restore with 18; same commands.
4. Point the application at `postgres-db-credentials` (its own repository: env from the Secret, or `uri`), start it,
   check it, then remove its PostgreSQL StatefulSet/Deployment. Keep the old PVC until the next backup has run.

**Not covered:** no backups yet (above); replicas protect against a node failure, not a dropped table. Volumes use
`Retain`: after deleting the Cluster or a PVC, the PV and its folder (`/var/local-path-provisioner` on that node)
remain; `$K get pv | grep local-path`, then delete the PV once the data is no longer needed. The 10Gi size is not
enforced by `local-path`; every volume shares mk4's one disk. A node that is gone keeps its instance Pending; deleting
that instance's PVC (or `kubectl cnpg destroy`) makes the operator rebuild it on another node from the primary.

## Redis (Valkey)

**One Redis for every application on mk4.** StatefulSet `valkey` in namespace `redis`: Valkey 9.1 (Redis 7.2
protocol), a primary and one replica, one pod on each of the two k3s workers (the control plane runs no workloads),
each with a 2Gi `local-path` volume (AOF every second plus RDB). Each pod runs Valkey, Sentinel, an HAProxy "master
proxy", the exporter and an Istio sidecar. Sentinel (quorum 2 of 2) promotes the replica after 5 s without an answer
from a crashed primary whose node is still up. **A lost worker is not failed over automatically:** its Sentinel goes
with it and the survivor cannot win a vote alone. If the primary's worker is down for good, promote the replica by hand:
`$K -n redis exec valkey-1 -c valkey -- sh -c 'REDISCLI_AUTH=$VALKEY_PASSWORD valkey-cli -p 26379 sentinel failover mk4'`.
A third worker brings automatic failover back (`replicaCount: 3`, `pdb.minAvailable: 2`). HAProxy in every pod forwards to whichever pod is primary right
now, so applications use one address and need no Sentinel support. A rolling update or pod deletion hands the primary
over first (preStop failover): measured on 2026-11-09, deleting the primary cost a writing client 2 failed writes over
about 4 s, after which writes resumed on the new primary through the same address.

**Security.** Namespace `redis` is in the mesh with STRICT mTLS (its own PeerAuthentication) and an enforced (not
dry-run) AuthorizationPolicy: only the tenants' namespaces, the gateway's TCP route and Prometheus get in, each on its
own ports; a pod without a sidecar is refused. NetworkPolicies add the same rules on the pod network. On top, every
application has its own ACL user:

- user `<name>` may `SELECT` only its own database numbers (Valkey ACL `db=`) and has no admin command (`-@admin`:
  CONFIG, SHUTDOWN, REPLICAOF, MONITOR, ACL, SAVE, ...) and no FLUSHALL or SWAPDB. FLUSHDB stays: it reaches only its
  own database;
- a random password, kept in the encrypted Terraform state, in Secret `redis/valkey-users` (key `<name>`); the server
  holds only its SHA-256 (`user` lines in `valkey.conf`);
- Secret `<ns>/redis-<name>-credentials` with `host` (`valkey-master.redis.svc.cluster.local`), `port`, `username`,
  `password`, `db` (its first database), `databases`, `uri` (`redis://<name>:<password>@valkey-master...:6379/<db>`).

Reserved users: `default` (admin: replication, Sentinel, HAProxy checks, exporter), `console` (RedisInsight: every
database, no admin command, no flush) and `argocd` (Argo CD, `argocd.tf`: database 7 only, no admin command). Pub/Sub channels are shared by every user (`&*`): channel names are not
isolated, keys are. Adding or changing a tenant rolls the StatefulSet (primary handed over first).

| Tenant (`redis_tenants`) | Namespace | Databases | Used by (reads `redis-<name>-credentials`) | Replaced |
|---|---|---|---|---|
| `jithendar` | `jithendar` | 0 | site cache, sessions, rate limits: `REDIS_URL` = `uri` (jithendar.com `minikube/30-jithendar-com.yaml`) | `jithendar-redis` |
| `practice` | `jithendar` | 1 | practice executor queue: `EXECUTOR_REDIS_URL` = `uri` (jithendar.com `minikube/practice-executor-40-executor.yaml`) | `practice-redis` |
| `harbor` | `harbor` | 2 core, 3 jobservice, 4 registry, 5 trivy | `password` (miniKube `deploy/minikube/harbor/values.yaml`) | `harbor-redis` databases 0, 1, 2, 5 |
| `argocd` (built in, `argocd.tf`) | `argocd` | 7 | Argo CD's cache: server, repo-server, application controller (`externalRedis`, Secret `argocd-redis-valkey` with `redis-username`/`redis-password`; `configs.params.redis.db`) | `argocd-redis` |
| `roboshop` | `roboshop-ai` | 6 | cart-service, shipping-service: host/port/username/password/db (roboshop-ai `argocd-minikube`) | `roboshop-ai/redis` |

No application on mk4 runs a Redis of its own any more.
Argo CD included: Valkey is a Terraform release, not an Argo CD Application, so the GitOps controller never depends on
something it deploys. Every key Argo CD writes has a TTL, so `volatile-lru` may evict it; Argo CD recomputes it.

## Argo CD

`argocd.tf`, release `argocd` in namespace `argocd` (in the mesh: Valkey is STRICT mTLS), at https://jithendar.com/argocd
(admin password: `kubectl --context mk4 -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d`).
`scripts/argocd/deploy.py` and `bootstrap-cluster.sh` no longer install it; they only check it is there.

Adopting an Argo CD installed earlier by `deploy.py` (Helm release `argocd`, own `argocd-redis`), once:

```bash
cd minikube-infrastructure
terraform import -var-file=vars/mk4.tfvars kubernetes_namespace_v1.argocd argocd
terraform import -var-file=vars/mk4.tfvars helm_release.argocd argocd/argocd
terraform apply -var-file=vars/mk4.tfvars   # rolls Valkey (new ACL user), then upgrades Argo CD: argocd-redis is removed
```

Argo CD's cache starts empty on Valkey: the first refresh of each application recomputes its manifests.

**Memory.** `maxmemory 512mb`, `maxmemory-policy volatile-lru`: only keys with a TTL are evicted (caches, sessions),
never queue entries (Harbor jobservice, the practice executor). When memory is full of keys without a TTL, writes fail
with OOM; `ValkeyMemoryHigh` fires at 85 %. Raise `maxMemory` and the 1Gi limit together.

| From | Address |
|---|---|
| applications | `uri` from `redis-<name>-credentials` (`valkey-master`, the current primary) |
| Sentinel-aware clients | `valkey-sentinel.redis.svc:26379`, master name `mk4` |
| browser | https://jithendar.com/redis/ (RedisInsight, preconfigured as `console`): gateway login `redis` from Secret `redis/redis-ui-auth`, plus the IP allowlist |
| redis-cli | `redis-cli --tls --sni redis.jithendar.com -h redis.jithendar.com -p 443 --user <name> --askpass` |

Both public ways in are refused on the gateway unless the client address is in `redis_allowed_ips`
(`vars/mk4.tfvars`); an empty list fails the plan. `redis.jithendar.com` needs a DNS record pointing at the host like
`postgres.jithendar.com` (jithendar.com repository, `scripts/cloudflare-dns-record.sh`). Passwords:
`$K -n redis get secret valkey-users -o jsonpath='{.data.<name>}' | base64 -d`; UI login:
`$K -n redis get secret redis-ui-auth -o jsonpath='{.data.password}' | base64 -d`.

**Health:** `$K -n redis exec valkey-0 -c valkey -- sh -c 'REDISCLI_AUTH=$VALKEY_PASSWORD valkey-cli -p 26379 sentinel master mk4'`
(also `sentinel ckquorum mk4`; `info replication` on the primary shows `connected_slaves:1`). Alerts (Prometheus job
`redis`): `ValkeyDown`, `ValkeyReplicaMissing`, `ValkeyMemoryHigh`, `ValkeyEvictingKeys`.

**First install, in stages:** the edge chart refuses to plan until the gateway has a `redis` login line, which needs
Secret `redis-ui-auth`:

```bash
export DOCKER_CONFIG=$(mktemp -d)   # only if ~/.docker/config.json names a credsStore that is not installed (OCI pull)
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars -target=helm_release.redis -target=kubernetes_secret_v1.redis_ui_auth -target=kubernetes_secret_v1.redis_app
python3 scripts/istio/gateway-basic-auth.py --context mk4
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars   # edge route + TLS host, access chart, Prometheus job
```

**HAProxy post-renderer.** The chart's HAProxy closes connections idle for 30 s (idle Pub/Sub subscribers, blocking
`BRPOP`). `deploy/minikube/redis/postrender-haproxy.sh` adds `timeout tunnel 1h` (Envoy's TCP idle timeout) and fails
the apply if a chart upgrade removes the line it expects: check the new chart's HAProxy config, then adjust the script.

### Moving an application (once each)

1. Its namespace's NetworkPolicy must allow egress to namespace `redis` on 6379 and 6380 (`valkey-master:6379` is the
   HAProxy master proxy on pod port 6380; jithendar restricts egress), and an
   Istio `Sidecar` egress list, if it has one (jithendar `75-mesh.yaml`), must include `redis/*`. The namespace must be
   meshed: `redis` refuses plain text.
2. Point the application at `redis-<name>-credentials` (its own repository: `uri`, or host/port/username/password/db).
   Caches and sessions (jithendar, roboshop) need no copy; a cutover only logs users out.
   Harbor's jobservice keeps queued and scheduled jobs in its Redis: copy old database 1 to new 3 first (scale
   jobservice to 0, then `MIGRATE valkey-master.redis.svc.cluster.local 6379 "" 3 5000 COPY AUTH2 harbor <password>
   KEYS ...` from `harbor-redis-0`, keys from `SCAN`), and map the databases 0→2, 1→3, 2→4, 5→5 in `harbor/values.yaml`.
   The practice queue: drain it (no queued jobs) before switching.
3. Start it, check it (`$K -n redis exec valkey-0 -c valkey -- sh -c 'REDISCLI_AUTH=$VALKEY_PASSWORD valkey-cli client
   list' | grep user=<name>`), then remove the old Redis Deployment/StatefulSet, Service, NetworkPolicy and Secret.

**Primary's node lost.** Sentinel promotes a replica within seconds; nothing to do. The lost pod stays Pending on its
node (its volume is pinned there). If the node is gone for good: `$K -n redis delete pvc data-valkey-<n>` (then the PV,
`Retain`) and delete the pod; it is rebuilt on another node and resyncs from the primary.

**Not covered:** no off-cluster backup of the AOF/RDB yet; replicas protect against a node failure, not a deleted key.

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
State: `s3://jithendardharmapuri/terraform/k3s-mk4.tfstate` (same bucket as the tools repository's `infrastructure/`, own key). The
jithendar.com repository must be checked out beside this one (or set `jithendar_com_dir`).

```bash
terraform -chdir=minikube-infrastructure init
terraform -chdir=minikube-infrastructure plan -var-file=vars/mk4.tfvars
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
```

Checks without a cluster: `terraform init -backend=false && terraform validate && terraform test`, and
`python3 -m pytest tests` from the repository root (renders every local chart).

Fresh cluster order: `./deploy-minikube.sh`, then this root (with `adopt_existing_traefik = false`; Traefik first,
because the Loki and Prometheus charts use its Middleware CRD), then `./bootstrap-cluster.sh` (application secrets and pull credentials; Argo CD itself is `argocd.tf`).

## CI

| Workflow | When | Runner | Does |
|---|---|---|---|
| `.github/workflows/check-mk-infrastructure.yml` | pull requests touching this folder, `deploy/minikube/{cert-manager,gatekeeper,istio,observability}`, the metrics-server values | GitHub-hosted, no secrets | fmt, validate, `terraform test`, `helm lint`/`template` of every local chart |
| `.github/workflows/create-mk-infrastructure.yml` ("Deploy minikube infrastructure") | push to `master` (same paths), or by hand (`plan_only` to see drift) | GitHub-hosted, environment `mk4` | prerequisite checks (no leftover Argo CD apps, Cloudflare Secret), fmt, validate, test, plan (in the run summary), apply, then checks all 27 releases are `deployed` and /grafana, /prometheus, /kiali answer |

Both check out `miniKube` and `jithendar.com` (public, `grafana/` only) side by side, so `jithendar_com_dir` keeps its
default. A dashboard change in jithendar.com does not trigger the deploy; run it by hand.

The deploy job runs only when repository variable `MINIKUBE_TERRAFORM_APPLY` is `true`. Its access:

- **mk4:** ServiceAccount `kube-system/github-minikube-terraform` (`ci/github-terraform-serviceaccount.yaml`), bound to
  `cluster-admin` (CRDs, ClusterRoles and webhooks need it). Its token is a secret of the environment `mk4`, which only
  `master` can deploy to, so pull request jobs never see it. API endpoint `MK4_API_SERVER`: the k3s API of the
  Hetzner master (`server:` of context `mk4`, port 6443); the old Minikube `https://jithendar.com:32771` is gone. Revoke: `kubectl --context mk4 -n kube-system delete secret github-minikube-terraform-token`.
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
