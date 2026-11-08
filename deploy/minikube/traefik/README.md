# Traefik and public ingress on mk4

Traefik is the ingress controller for every public route on mk4: the jithendar.com site, `/argocd`, `/grafana`,
`/prometheus`, `/kiali`, `/loki`, Harbor and the rest. It is a Terraform release:
[`minikube-infrastructure/traefik.tf`](../../../minikube-infrastructure/traefik.tf), official chart 42.0.0 (Traefik
v3.7), namespace `traefik`, values in [`values.yaml`](values.yaml). Nothing else installs or upgrades it: not
`bootstrap-cluster.sh`, not a `helm upgrade` by hand (it would leave Terraform's state behind the live release).

The release creates IngressClass `traefik` (not the cluster default), HTTP NodePort 30080 and HTTPS NodePort 30443.
The Traefik dashboard is not exposed. `expose-ingress.sh` on the host forwards 80/443 to those NodePorts.

## Apply a change

Edit `values.yaml`, then either push to `master` (the "Create minikube infrastructure" workflow applies it once
`MINIKUBE_TERRAFORM_APPLY` is on) or, from the repository root:

```bash
terraform -chdir=minikube-infrastructure plan  -var-file=vars/mk4.tfvars
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
```

Traefik is the only public entry point, so a values change restarts every site for the length of a rollout (one
replica). Read the plan: a change to `ports` or `service` can drop connections for longer.

**Adoption.** mk4's release was installed by `bootstrap-cluster.sh` on 2026-10-27, before Terraform owned it.
`vars/mk4.tfvars` sets `adopt_existing_traefik = true`, so the first apply imports `traefik/traefik` into state and
upgrades it in place (revision 3). A read-only plan on 2026-11-08 showed `1 to import, 0 to add, 1 to change`; the only
value that differs from what runs today is `websecure.transport.respondingTimeouts.readTimeout: 900s` (for Harbor
pushes). Revision 2 (2026-11-04) is `failed` (context canceled) although its PROXY-protocol arguments are live; the
apply replaces it. On a fresh cluster set `adopt_existing_traefik = false`.

## Fresh cluster order

1. `./deploy-minikube.sh` creates the cluster.
2. `terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars` installs Traefik first: the Loki and
   Prometheus releases depend on it because their Middlewares need its CRDs.
3. `./bootstrap-cluster.sh` installs Argo CD and its ingress. It stops if IngressClass `traefik` or the Middleware CRD
   is missing.
4. On the host: `bash deploy/minikube/expose-ingress.sh`.

## Host forwarding

`expose-ingress.sh` must run on the **Linux Docker host**, with local Docker and kubectl access. It follows the
installer's selected Minikube profile and starts a small HAProxy container that forwards host 80/443 to the Minikube
node's 30080/30443. TLS terminates in Traefik; HAProxy holds no certificates. The proxy restarts with Docker; rerun
the script after replacing the Minikube profile. Its configuration is under `~/.minikube/roboshop-installer/ingress-edge/`.

## Verify and troubleshoot

```bash
kubectl --context mk4 -n traefik get pods,svc
kubectl --context mk4 get ingressclass traefik
kubectl --context mk4 -n traefik logs deployment/traefik --tail=100
helm --kube-context mk4 -n traefik history traefik
# On the host:
docker logs roboshop-ingress-edge-$(id -u) --tail=100
ss -ltnp '( sport = :80 or sport = :443 )'
# From anywhere:
curl -I https://jithendar.com/grafana/login
```

An HTTP 404 before routes exist is normal: Traefik is reachable. A timeout means DNS, firewall or host forwarding.
If the proxy failed because a port was occupied, resolve the conflict, then `docker rm -f roboshop-ingress-edge-$(id -u)`
and rerun `expose-ingress.sh`.

Traefik publishes `jithendar.com` as the ingress status hostname: the NodePort Service has no load-balancer address,
and copying its status would leave ingresses permanently Progressing in Argo CD. If the public hostname changes,
update `providers.kubernetesIngress.ingressEndpoint.hostname`.

## Real client IPs (PROXY protocol)

HAProxy sends a PROXY protocol v2 header and Traefik trusts it from `192.168.49.0/24` (the minikube Docker network),
so `X-Forwarded-For`, Traefik's `ipAllowList` and the apps' logs see the visitor's address instead of the node's.
Order matters: Traefik first (a Terraform apply; Traefik then accepts connections with or without the header), then
`expose-ingress.sh` on the host (it refuses to run until Traefik accepts the header). To undo, reverse the order:
remove `send-proxy-v2` and rerun `expose-ingress.sh`, then remove `proxyProtocol` from `values.yaml` and apply.
Anything that can reach a node's NodePort from inside the minikube network could also send a forged header, so an
IP allowlist is one layer, never the only one.

Chart reference: [official Traefik Helm chart](https://github.com/traefik/traefik-helm-chart).
