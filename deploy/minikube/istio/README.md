# Istio on mk4: the public edge

The Istio ingress gateway (`ingressgateway.yaml`, release `istio-ingressgateway`) is mk4's edge. The host's HAProxy
(`../expose-ingress.sh`) forwards 80/443 to its NodePorts 31080/31443 with a PROXY protocol v2 header. Release
`istio-edge` (`edge/`, `minikube-infrastructure/istio.tf`) holds:

| What | Where |
|---|---|
| Gateway `istio-ingress/public`: one HTTPS server for jithendar.com, www, registry., jfrog.; a TLS server for db.; port 80 redirects | `edge/templates/gateway.yaml` |
| Wildcard certificate `jithendar.com` + `*.jithendar.com` (DNS-01), Secret `jithendar-com-gateway-tls` | `edge/templates/certificate.yaml` |
| The jithendar.com routing table: www → apex, redirects, one delegation per path prefix, the site last | `edge/templates/root-virtualservice.yaml`, `edge/values.yaml` `root` |
| Logins (basic auth) for /prometheus, /loki, /opensearch, /opensearch-dashboards, by path prefix | `edge/templates/basic-auth.yaml`, `basicAuth`; users from Secret `gateway-basic-auth` |
| /opensearch read-only (GET, HEAD, POST to `_search`/`_msearch`/`_count`) | `edge/templates/read-only.yaml` |
| Visitor address: PROXY protocol first on 80/443; X-Forwarded-For and X-Real-IP replaced by it | `edge/templates/proxy-protocol.yaml`, `forwarded-for.yaml` |
| ALPN `postgresql` for db.jithendar.com, compression | `edge/templates/tls-alpn.yaml`, `compression.yaml` |
| Delegates for Kiali and Grafana (their charts only render Ingresses) | `edge/templates/route-*.yaml` |

Each application owns a delegate VirtualService for its prefix (no hosts, no gateways): moks (`/moks`, `/opensearch`,
`/opensearch-dashboards`), roboshop-ai (`/roboshop`), jithendar.com (the site, `/db`), Prometheus, Loki, Argo CD and
Vault here. Its own hostname gets its own VirtualService on `istio-ingress/public`: Harbor (registry.), JFrog (jfrog.),
jithendar.com (db., TCP). One root avoids Istio's merge order for VirtualServices of one host: whichever was created first
would win, and the site's `/` would shadow every later prefix.

A new public prefix: add it to `edge/values.yaml` `root.delegates` (longer prefixes first) and give the application a
delegate VirtualService. A new hostname: add `<namespace>/<host>` to `gateway.https`.

## Logins

`python3 scripts/istio/gateway-basic-auth.py` reads each login's source Secret (`basicAuth[].from`, the same
`kubernetes.io/basic-auth` Secrets Traefik used) and writes `istio-ingress/gateway-basic-auth` as `{SHA}` htpasswd
lines, the only format Envoy's basic_auth accepts. Then `terraform apply`. Rerun both after changing a password. A
missing source Secret gives a locked line (today: `monitoring/loki-read-auth`), and Terraform refuses to plan without
the Secret, so a path never opens by accident. The hashes are in the Terraform state and the EnvoyFilter; the
passwords are not.

## Leaving Traefik (once, in this order)

`K="kubectl --context mk4"`. Each step keeps every route working; check before the next.

1. **Logins:** `python3 scripts/istio/gateway-basic-auth.py` (`--dry-run` first).
2. **Applications first.** Their delegates change nothing until the root exists, and their NetworkPolicies still
   admit Traefik:
   - jithendar.com: `$K apply -k minikube`, `scripts/manifests.sh db-access | $K apply -f -`;
   - moks: push, sync `moks-routing` and `moks-foundation`;
   - roboshop-ai: merge `argocd-minikube/apps/routing` to master, sync `roboshop-routing`;
   - this repo: push, sync `harbor`, `$K apply -k deploy/minikube/argocd`.
3. **Edge:** `terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars` (`traefik_enabled = true`).
   Gateway `public`, the routing table and logins go live, and the Kiali, Grafana, Prometheus and Loki Ingresses go
   away. Traefik's hand-off (priority 1) sends whatever it no longer routes to the gateway. Check the gateway took the
   config: `$K -n istio-system logs deploy/istiod --since=5m | grep -i -E 'nack|rejected'` must be empty. Before going
   on, test through the gateway itself:
   `$K -n istio-ingress port-forward svc/istio-ingressgateway 18443:443`, then
   `curl --resolve jithendar.com:18443:127.0.0.1 https://jithendar.com:18443/<path>` for `/`, `/argocd/`,
   `/grafana/api/health`, `/kiali/`, `/prometheus/-/ready` (401 with `WWW-Authenticate`, 200 with `-u`), `/moks/`,
   `/roboshop/` and `/opensearch` (401). `-X PUT` with the login gives 403. Registry `/v2/` gives Harbor's own 401.
4. **Traefik's own routes:** delete them, and the hand-off sends their traffic to the gateway too:
   - `$K -n argocd delete ingress argocd argocd-http; $K -n argocd delete middleware argocd-https`;
   - `$K -n harbor delete ingress harbor-ingress harbor-http harbor-shortcut; $K -n harbor delete middleware harbor-https harbor-shortcut`;
   - `$K -n moks delete ingress moks moks-http; $K -n moks delete ingressroute opensearch opensearch-dashboards; $K -n moks delete middleware https-redirect opensearch-auth opensearch-strip-prefix opensearch-dashboards-auth`;
   - `$K -n roboshop-ai delete ingress roboshop roboshop-http; $K -n roboshop-ai delete middleware https-redirect roboshop-strip-prefix`.

   Until step 5 the applications see Traefik's pod address instead of the visitor's: /db and db.jithendar.com refuse
   everyone, and per-IP rate limits share one bucket. Keep this short.
5. **Host:** push this repository, then on the Hetzner host
   `cd miniKube && git pull --ff-only && bash deploy/minikube/expose-ingress.sh`. HAProxy now sends 80/443 to the gateway.
   Check that `/`, `/db/` from an allowed address, `psql "host=db.jithendar.com port=443 … sslnegotiation=direct"`, and
   `docker push registry.jithendar.com/…` all work, and that the site logs your real address. A spoofed
   `X-Forwarded-For` must not change it.
   *Way back:* `git checkout` the previous `expose-ingress.sh` and rerun it. Traefik is still installed.
6. **Uninstall Traefik:** set `traefik_enabled = false` in `minikube-infrastructure/vars/mk4.tfvars` and `terraform apply`.
   That removes the Helm release and the hand-off routes, and the gateway now requires the PROXY header. Then delete
   what Helm leaves behind: `$K delete ns traefik` and `$K get crd -o name | grep traefik.io | xargs $K delete`
   (after the jithendar.com objects in its README, "Leaving Traefik").
7. **Cleanup changes:**
   - moks `mesh.edgeMtls: true`, `networkPolicy.ingressNamespaces: [istio-ingress]`, and the traefik kinds and
     destination removed from `deploy/argocd/minikube/project.yaml`;
   - jithendar.com: its README's "Leaving Traefik" (STRICT);
   - here: delete `deploy/minikube/traefik/`, `namespaces/traefik.yaml`, `traefik.tf` with `traefik_enabled` and
     `adopt_existing_traefik`, the `traefik-handoff-*` templates and `tests/test_harbor.py::test_traefik_reads_large_layers`.
