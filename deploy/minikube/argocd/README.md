# Argo CD at https://jithendar.com/argocd/

Installed by Terraform: release `argocd` (chart `argo-cd` 10.10.5) in namespace `argocd`,
[`minikube-infrastructure/argocd.tf`](../../../minikube-infrastructure/argocd.tf), values in `values.yaml` here.

```bash
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
```

`./bootstrap-cluster.sh` and `./deploy-argocd.sh` no longer install it; they stop with an error if it is missing.

## Redis

Argo CD runs no Redis pod. Its cache is on the shared Valkey in namespace `redis`
([redis.tf](../../../minikube-infrastructure/redis.tf)):

| | |
|---|---|
| Address | `valkey-master.redis.svc.cluster.local:6379` (HAProxy to the current primary; failovers keep the address) |
| Login | ACL user `argocd`, Secret `argocd/argocd-redis-valkey` (`redis-username`, `redis-password`), written by Terraform |
| Database | 7 only (`configs.params.redis.db`); no admin command, no FLUSHALL |
| Transport | Istio mTLS: namespace `argocd` is meshed (`istio-injection=enabled`) and admitted by Valkey's AuthorizationPolicy |

Database 7 and the name `argocd` are reserved in `redis_tenants`. Every cache key has a TTL, so Valkey's `volatile-lru`
may evict it under memory pressure; Argo CD recomputes it.

## Access

The Istio gateway terminates TLS and routes `/argocd` (edge routing table, delegate `argocd/argocd`) to the
VirtualService rendered from `extraObjects` in `values.yaml`. Argo CD serves plain HTTP under `/argocd` itself
(`server.rootpath`, `server.basehref`); do not strip the prefix.

Sign in as `admin`:

```bash
kubectl --context=mk4 -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 --decode; echo
```

Change it on the User Info page afterwards. Anonymous access is disabled. CLI:

```bash
argocd login jithendar.com --grpc-web --grpc-web-root-path /argocd --username admin
```

## Troubleshooting

```bash
kubectl --context=mk4 -n argocd get pods,virtualservice
kubectl --context=mk4 -n argocd logs deployment/argocd-server --tail=100 | grep -i redis
```

`WRONGPASS` or `NOPERM`: the ACL user is not loaded yet (Valkey rolls after a tenant change) or `redis.db` is not 7.
Connection reset before any reply: the pod has no Istio sidecar (restart it after labelling the namespace) or namespace
`argocd` is missing from the `redis-access` release's `clientNamespaces`. A 503 at /argocd means argocd-server is still
starting.
