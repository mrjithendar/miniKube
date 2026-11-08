> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. Use the Argo CD catalog and the five supported commands
> documented in `docs/operations/scripts.md`. Historical examples below are not
> runnable deployment instructions.

# Argo CD at https://jithendar.com/argocd/

From the repository root:

```bash
bash deploy/minikube/deploy-argocd.sh
```

Uses the existing Minikube context `mk4` (including the installer's saved profile),
Traefik and `letsencrypt-prod`. Override context with `KUBE_CONTEXT=your-context`.
The existing dependency installer verifies/installs Helm and kubectl when needed.
DNS must point jithendar.com to the server, with ports 80/443 exposed to Traefik.

The script installs/updates pinned chart 10.10.5 in namespace `argocd`, then applies
HTTPS ingress and a namespace-local Certificate. It submits changes without waiting
for rollout or certificate issuance. It does not change WordPress, Grafana or
RoboShop routes, reset credentials or configure automatic application syncing.

Sign in as `admin`. Retrieve the initial password after the server starts:

```bash
kubectl --context=mk4 -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 --decode; echo
```

Change the initial password in Argo CD's User Info page. The initial secret is not
the current password after a password change; the script never prints it itself.
Anonymous access is disabled. Argo CD has cluster management permissions, so only
trusted administrators should receive access.

Optional CLI login (uses gRPC-Web for the shared HTTPS subpath):

```bash
argocd login jithendar.com --grpc-web --grpc-web-root-path /argocd --username admin
```

TLS terminates at Traefik. Argo CD serves HTTP internally, with both rootpath and
basehref set to `/argocd`. Do not add StripPrefix middleware. A certificate exists
in `argocd` because Kubernetes Ingress cannot reference another namespace's TLS
Secret; this does not replace the certificates owned by other applications.

Troubleshooting:

```bash
kubectl --context=mk4 -n argocd get pods,ingress,certificate
kubectl --context=mk4 -n argocd describe certificate jithendar-com
kubectl --context=mk4 -n argocd logs deployment/argocd-server --tail=100
```

A pending certificate requires working DNS, public port 80 and a ready issuer.
A 503 usually means argocd-server is still starting; check pods and logs. A redirect
loop or missing assets means rootpath/basehref or ingress prefix handling differs
from these values. The Helm install is single-replica for development; controller
and repo-server memory limits are each 1 GiB.

Reference: [official Argo CD subpath ingress guidance](https://argo-cd.readthedocs.io/en/stable/operator-manual/ingress/).
