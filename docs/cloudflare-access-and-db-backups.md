# Cloudflare proxy, Cloudflare Access and PostgreSQL backups on mk4

The manifests and Terraform for these are in place, all **off by default**: merging changes nothing on the cluster until
the variables below are set. The two parts are independent; do backups first.

| Part | Switch (minikube-infrastructure/vars/mk4.tfvars) | Code |
|---|---|---|
| PostgreSQL backups to R2 | `postgres_backups` | `postgres.tf`, `deploy/minikube/postgres/cluster.yaml` (`backups`), `scripts/postgres/backup-secret.sh` |
| Second PostgreSQL instance | always on (`instances: 2`) | `deploy/minikube/postgres/cluster.yaml` |
| Cloudflare proxy (visitor address) | `cloudflare_proxied` | `deploy/minikube/istio/edge/templates/cloudflare-client-address.yaml` |
| Cloudflare Access on admin tools | `cloudflare_access` | `deploy/minikube/istio/edge/templates/cloudflare-access.yaml` |

## 1. PostgreSQL backups (R2)

Base backups every night at 02:00 UTC plus the continuous WAL archive, through the Barman Cloud plugin (release
`barman-cloud` in `cnpg-system`; the operator's built-in `barmanObjectStore` is removed in CloudNativePG 1.31). Restore
to any moment in the last 14 days (`retention`).

1. Cloudflare dashboard → R2 → **Create bucket**, e.g. `jithendar-postgres-backups`. No public access, no custom domain.
2. R2 → **Manage API tokens** → **Create API token**: permission *Object Read & Write*, **only that bucket**. Copy the
   Access Key ID and Secret Access Key (shown once). Note the account ID (R2 overview, right-hand side).
3. Store the keys in the cluster (prompts; nothing is echoed or written to disk):
   ```bash
   scripts/postgres/backup-secret.sh                # current kubectl context
   KUBE_CONTEXT=mk4 scripts/postgres/backup-secret.sh
   ```
4. In `vars/mk4.tfvars`:
   ```hcl
   postgres_backups = {
     account_id = "<32-hex account ID>"
     bucket     = "jithendar-postgres-backups"
   }
   ```
   Terraform refuses to apply if step 3 was skipped: archiving without credentials would keep every WAL file on the
   node's disk until it fills.
5. Apply (CI `create-mk-infrastructure`, or `terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars`).
   The same apply adds the second instance (a replica on another worker; the operator clones it from the primary, a few
   minutes for ~0.4 GB).
6. Check:
   ```bash
   kubectl -n postgres-db get cluster postgres-cluster          # INSTANCES 2, READY 2
   kubectl -n postgres-db get backup                            # the first one runs at once (immediate: true): completed
   kubectl -n postgres-db get cluster postgres-cluster \
     -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}'   # True
   ```
   If the first backup fails with a checksum or `NotImplemented` error from R2, the plugin's boto3 sends a header R2
   does not accept: look at `kubectl -n cnpg-system logs deploy/barman-cloud` and the instance pod's `plugin-barman-cloud`
   container.
7. Once a quarter, test a restore into a scratch cluster (CloudNativePG `bootstrap.recovery` from the same ObjectStore).
   A backup nobody has restored is a hope, not a backup.

## 2. Cloudflare proxy and Access

Order matters: each step keeps the site working.

1. **SSL/TLS → Overview → Full (strict).** The origin has a valid Let's Encrypt certificate. *Flexible* would loop
   redirects (the gateway sends http to https).
2. **Zero Trust** (one.dash.cloudflare.com): pick a team name (the team domain is `<team>.cloudflareaccess.com`). Under
   Settings → Authentication add a login method (One-time PIN by email works with no setup; Google if preferred).
3. Access → Applications → **Add an application → Self-hosted**, name `jithendar.com admin tools`. Destinations (one
   per path): `jithendar.com/argocd`, `/kiali`, `/grafana`, `/prometheus`, `/loki`, `/opensearch-dashboards`,
   `/opensearch`, `/postgres`, `/redis`, `/ui`, `/v1`. Policies:
   - *Owner*: Allow, Include → Emails → your address.
   - *CI*: **Service Auth**, Include → Service Token → the token from step 5.

   Copy the application's **Application Audience (AUD) tag** (Overview tab).
4. Two more self-hosted applications with one policy each, action **Bypass**, Include → Everyone, for the machine
   endpoints that have their own gateway login: `jithendar.com/prometheus/api/v1/write` and
   `jithendar.com/loki/api/v1/push` (the EKS cluster's Prometheus agent and Alloy push there). The more specific path wins.
5. Access → Service credentials → **Create service token** `moks-ci`. Put the Client ID and Secret in the **moks**
   repository's Actions secrets as `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` (its deploy job sends them to
   Argo CD; without them the job keeps working until Access is enforced, then gets 403).
6. **DNS**: turn the proxy (orange cloud) **on** for `jithendar.com` and `www` only. Keep these **DNS-only**:
   `registry` (Harbor pushes exceed Cloudflare's 100 MB request limit), `postgres` and `redis` (TLS-wrapped TCP, not
   HTTP), `jfrog`. Delete `db` (nothing serves it any more: HTTPS fails, HTTP answers 404).
7. In `vars/mk4.tfvars`: `cloudflare_proxied = true`, apply. The gateway now takes the visitor address from
   `CF-Connecting-IP` (only from Cloudflare's ranges; anyone else sending that header gets 403). Check the site's
   access log shows your own address, not a Cloudflare one:
   `kubectl -n jithendar logs deploy/jithendar-com -c web --since=2m | grep -o '"ip":"[^"]*"' | sort | uniq -c`.
   Until step 8, `/postgres` and `/redis` answer 403 (their IP allowlists now only see Cloudflare's addresses).
8. Add the Access settings and apply:
   ```hcl
   cloudflare_access = {
     team_domain = "<team>.cloudflareaccess.com"
     audiences   = ["<AUD tag from step 3>"]
   }
   ```
   This enforces the Access token on those paths at the gateway and switches off the `/postgres` and `/redis` UI
   allowlists (`postgres.jithendar.com` and `redis.jithendar.com` keep theirs).
9. Verify:
   ```bash
   # Through Cloudflare, not signed in: a redirect to the Access login
   curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://jithendar.com/argocd/
   # Around Cloudflare, straight to the server: refused by the gateway
   curl -s -o /dev/null -w '%{http_code}\n' --resolve jithendar.com:443:46.224.66.128 https://jithendar.com/argocd/   # 403
   # The site itself is unaffected
   curl -s -o /dev/null -w '%{http_code}\n' https://jithendar.com/                                                 # 200
   ```
   In a browser, `/argocd` asks for the Access login, then Argo CD's own.
10. Optional, for speed: Caching → **Cache Rules** → *Eligible for cache*, Edge TTL **Use cache-control header**, when
    hostname is `jithendar.com` and the path does not start with `/admin`, `/api`, `/account`, `/login`, `/preview`,
    `/setup` or any admin-tool prefix above. Public pages already send `s-maxage=300, stale-while-revalidate`, so
    Cloudflare then serves them from the nearest edge; pages for signed-in users send `private`/`no-store` and are
    never cached.

### Rolling back

- Access: remove `cloudflare_access`, apply (the allowlists come back). Then the application in Zero Trust.
- Proxy: grey-cloud the two records first, then `cloudflare_proxied = false`, apply.
- Backups: remove `postgres_backups`, apply. The bucket keeps what is there.
