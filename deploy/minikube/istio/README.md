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
| ALPN `postgresql` for postgres.jithendar.com, compression | `edge/templates/tls-alpn.yaml`, `compression.yaml` |
| Delegates for Kiali and Grafana (their charts only render Ingresses) | `edge/templates/route-*.yaml` |

Each application owns a delegate VirtualService for its prefix (no hosts, no gateways): moks (`/moks`, `/opensearch`,
`/opensearch-dashboards`), roboshop-ai (`/roboshop`), jithendar.com (the site), Prometheus, Loki, Argo CD and
Vault here. Its own hostname gets its own VirtualService on `istio-ingress/public`: Harbor (registry.), JFrog (jfrog.),
PostgreSQL (postgres., TCP), Redis (redis., TCP). One root avoids Istio's merge order for VirtualServices of one host: whichever was created first
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

## Mesh security (`mesh/`, release `istio-mesh`)

| What | Where |
|---|---|
| STRICT mTLS for every namespace without its own PeerAuthentication (`istio-system/default`); jithendar, moks and roboshop-ai keep a STRICT one of their own, none has a plain-text port | `mesh/templates/peerauthentication.yaml` |
| `allow-known-callers` in argocd, cert-manager, harbor, monitoring, observability: only the namespace itself and the listed namespaces' identities | `mesh/values.yaml` `authorization`; the applications keep theirs in their repositories |
| The gateway's access log, JSON with Alloy's allowed fields only (no address, path or header) | `mesh/templates/telemetry.yaml`, provider `edge-access-log` in `istiod.yaml` |
| Two gateway replicas with a PDB, two istiod replicas (chart PDB), Kiali meshed by pod label | `ingressgateway.yaml`, `istiod.yaml`, `kiali.yaml` |

Every client of a meshed pod is meshed: kubelet probes go to the sidecar's :15020, Prometheus scrapes through its own
sidecar (no `includeOutboundIPRanges: ""`: that made scrapes plain text), Kiali has a sidecar. Before adding a client
outside the mesh, check plain text is gone:
`sum by (destination_workload_namespace,destination_workload) (increase(istio_requests_total{reporter="destination",connection_security_policy="none"}[1h]))`
must be empty. The unmeshed namespaces moks-terminals, moks-diagnostics and moks-lab cannot call meshed Services.

**AuthorizationPolicies start as dry runs** (`istio.io/dry-run: "true"`): nothing is refused, Envoy counts what would
be. `python3 scripts/istio/authz-dry-run-report.py` prints the non-zero counts (exit 1). After a day at 0, enforce:
`authorization.dryRun: false` here, `mesh.authorization.dryRun: false` in moks, and delete the annotation in
jithendar.com `minikube/75-mesh.yaml` and roboshop-ai `argocd-minikube/apps/mesh/authorizationpolicy.yaml`. A new
caller across namespaces needs its namespace added to the callee's list first.

## Traefik (gone, 2026-11-09)

The host's HAProxy forwards to the gateway, the gateway requires the PROXY header, and Traefik's release, namespace and
routes are deleted. Left on mk4 until the next `terraform apply` of `prometheus` and `loki`: their old Traefik
Middlewares (Helm deletes them on upgrade). **Only after that apply**, delete the CRDs:
`kubectl --context mk4 get crd -o name | grep traefik.io | xargs kubectl --context mk4 delete`. Deleting them first
breaks the upgrade: Helm cannot build the previous release's Middleware objects without their CRD.
