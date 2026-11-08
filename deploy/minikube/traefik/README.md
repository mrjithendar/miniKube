> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. Use the Argo CD catalog and the five supported commands
> documented in `docs/operations/scripts.md`. Historical examples below are not
> runnable deployment instructions.

# Traefik and public ingress on Vultr

The `ingressclass "traefik" not found` error means the controller has not been installed. cert-manager issues certificates; it does not install an ingress controller.

## Complete setup on Vultr

Run these commands **on your Vultr server**, from the repository root, after updating your checkout with these files:

```bash
bash deploy/minikube/deploy-traefik.sh
bash deploy/minikube/expose-ingress.sh
bash deploy/minikube/deploy-cert-manager.sh
bash deploy/minikube/deploy-grafana.sh
```

Then open **https://jithendar.com/grafana/**. DNS must point to Vultr, and the Vultr firewall must allow inbound TCP **80 and 443**. Keep port 80 available for Let's Encrypt renewal. Existing listeners on those ports must be reconciled before running the host proxy; the script does not stop unrelated services or delete clusters.

`deploy-traefik.sh` installs the official pinned Traefik Helm chart 42.0.0 into namespace `traefik`. It creates IngressClass `traefik` without making it the cluster default, exposes HTTP NodePort 30080 and HTTPS NodePort 30443, and waits for readiness. Requires Kubernetes 1.30+ (the existing Minikube setup uses 1.37). The Traefik dashboard is not publicly exposed.

`expose-ingress.sh` must run on the **Linux Docker host**, with local Docker and kubectl access. It follows the installer's selected Minikube profile and starts a small HAProxy Docker container to forward host 80/443 to the Minikube node's 30080/30443. This supplies the missing route through Docker's private network without recreating Minikube. TLS terminates in Traefik; HAProxy holds no certificates. TCP forwarding also supports Grafana WebSockets.

The proxy restarts with Docker. Rerun the host script after replacing the Minikube profile. An existing proxy on a different Docker network is left intact with instructions, rather than silently replaced. The configuration is stored under `~/.minikube/roboshop-installer/ingress-edge/`. Removing the Minikube cluster does not automatically remove this host proxy.

## From your Mac

The Helm scripts work through your remote kubeconfig. The host routing script does **not** run on your Mac.

```bash
# Mac: select the context imported by kubeconfig.sh.
kubectl config get-contexts
KUBE_CONTEXT=mk4 bash deploy/minikube/deploy-traefik.sh

# SSH to Vultr, enter your repository, and run there:
bash deploy/minikube/expose-ingress.sh

# Mac: after host routing is ready, use the same context:
KUBE_CONTEXT=mk4 bash deploy/minikube/deploy-cert-manager.sh
KUBE_CONTEXT=mk4 bash deploy/minikube/deploy-grafana.sh
```

Replace `mk4` with the actual context if different. On the host, `PROFILE=...` selects a different base profile; its saved selected-profile mapping is followed automatically. No SSH passwords are stored or passed to scripts.

## Verify and troubleshoot

Use `--context=YOUR_CONTEXT` with kubectl if necessary:

```bash
kubectl -n traefik get pods,svc
kubectl get ingressclass traefik
kubectl -n traefik logs deployment/traefik --tail=100
# On Vultr:
docker logs roboshop-ingress-edge-$(id -u) --tail=100
ss -ltnp '( sport = :80 or sport = :443 )'
# From either host:
curl -I http://jithendar.com/
curl -I https://jithendar.com/grafana/login
```

An HTTP 404 before deploying routes is normal: it shows Traefik is reachable. HTTPS must have a ready certificate before browsers trust it. A timeout means DNS/firewall/host forwarding still needs attention. If the host firewall blocks traffic to the Docker bridge, inspect its forwarding rules rather than disabling the firewall globally.

If the proxy failed because a port was occupied, resolve that conflict and remove only the failed proxy before rerunning:

```bash
# Stops this site's host ingress; use only when replacing/removing this proxy.
docker rm -f roboshop-ingress-edge-$(id -u)
bash deploy/minikube/expose-ingress.sh
```

See [Grafana](../monitoring/grafana/README.md) and [cert-manager](../cert-manager/README.md) for certificate and login troubleshooting. Chart reference: [official Traefik Helm chart](https://github.com/traefik/traefik-helm-chart).

Traefik publishes `jithendar.com` as the ingress status hostname. The NodePort service has no load-balancer address, so copying its status would leave ingresses permanently Progressing in Argo CD. If the public hostname changes, update `providers.kubernetesIngress.ingressEndpoint.hostname` in `values.yaml`.


## Real client IPs (PROXY protocol)

HAProxy sends a PROXY protocol v2 header and Traefik trusts it from `192.168.49.0/24` (the minikube Docker network),
so `X-Forwarded-For`, Traefik's `ipAllowList` and the apps' logs see the visitor's address instead of the node's.
Order matters: `deploy-traefik.sh` first (Traefik then accepts connections with or without the header), then
`expose-ingress.sh` on the host (it refuses to run until Traefik accepts the header). To undo, reverse the order:
remove `send-proxy-v2` and rerun `expose-ingress.sh`, then remove `proxyProtocol` and rerun `deploy-traefik.sh`.
Anything that can reach a node's NodePort from inside the minikube network could also send a forged header, so an
IP allowlist is one layer, never the only one.
