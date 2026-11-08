> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. This component is a Terraform Helm release:
> `minikube-infrastructure/README.md`. Historical examples below are not
> runnable deployment instructions.

# Grafana at https://jithendar.com/grafana/

From the repository root on the Minikube host:

```bash
bash deploy/minikube/deploy-traefik.sh
bash deploy/minikube/expose-ingress.sh  # On the Vultr Linux host only
bash deploy/minikube/deploy-cert-manager.sh
bash deploy/minikube/deploy-grafana.sh
```

Requires Helm, kubectl, a running cluster, the **Traefik ingress controller**, and a default StorageClass (Minikube normally supplies one). DNS for `jithendar.com` must resolve to your server; public ports **80 and 443 must reach Traefik inside Minikube**. A private Minikube IP or NodePort alone does not provide this routing. The [Traefik setup scripts](../../traefik/README.md) install the controller and configure host forwarding. They do not modify the Vultr firewall. Keep port 80 reachable for automatic certificate renewal.

The script follows the installer's selected `mk4` profile. For another base profile set `PROFILE`; from your laptop with the imported kubeconfig, use `KUBE_CONTEXT=<context-name>`. All commands target that context. Example:

```bash
kubectl config get-contexts
KUBE_CONTEXT=mk4 bash deploy/minikube/deploy-grafana.sh
```

## What is deployed

- Pinned Grafana community Helm chart **14.0.1**, release `grafana`, namespace `monitoring`.
- One Grafana replica with a **2 GiB persistent volume** for dashboards, users and settings. This is a single-instance sandbox deployment; back up the volume before resetting Minikube.
- A ClusterIP service and a Traefik HTTPS Ingress for `/grafana` with Prefix matching. No rewrite or StripPrefix middleware is needed. Other website paths remain available for RoboShop's own ingress.
- TLS from the shared `jithendar/jithendar-com` certificate through Traefik's default TLSStore; this chart requests no Certificate.
- Secure cookies, anonymous access disabled, sign-up disabled, and the shared `monitoring-auth` administrator credentials. HTTPS terminates at Traefik; in-cluster traffic to Grafana uses HTTP.

The HTTPS-only Traefik router does not add a new HTTP redirect. Open the HTTPS URL directly. An existing controller-wide HTTP-to-HTTPS redirect can remain in place.

The installer waits for both Grafana and its certificate. Cluster readiness does not verify the external firewall: check the public endpoint too:

```bash
curl -I https://jithendar.com/grafana/login
```

## Login

Username: `admin`. Retrieve the initial password using the context printed by the script:

```bash
kubectl --context=YOUR_CONTEXT -n monitoring get secret monitoring-auth \
  -o jsonpath='{.data.password}' | base64 --decode; echo
```

The Grafana installer synchronizes administrator user ID 1 with the shared Secret on each deployment. A password changed only in Grafana’s UI is therefore replaced on the next deployment. Manage this credential through the shared Secret; see [shared credentials](../README.md#shared-login-secret).

## Connect metrics

Run `bash deploy/minikube/deploy-prometheus.sh`, then rerun `bash deploy/minikube/deploy-grafana.sh`. Grafana provisions the default **Prometheus** data source using `http://prometheus.monitoring.svc.cluster.local:9090/prometheus`. In Explore, query `up` to verify it. The Grafana installer does not itself install Prometheus; until Prometheus exists the data source will be unavailable. See [Prometheus setup](../prometheus/README.md) for scrape targets and public login details.

Edit `values.yaml` to configure Grafana, storage, or resources, then rerun the installer. When changing the domain/path, update the ingress, `grafana.ini.server.root_url`, and shared certificate together. Avoid changing the pinned chart version without reviewing its upgrade notes.

## Troubleshooting

Use the same context as the installer (`kubectl --context=YOUR_CONTEXT ...`):

```bash
kubectl -n monitoring get pods,pvc,svc,ingress
kubectl -n monitoring describe pod -l app.kubernetes.io/name=grafana
kubectl -n monitoring logs deployment/grafana -c grafana --tail=100
kubectl -n jithendar get certificate,certificaterequest,orders,challenges
kubectl -n jithendar describe certificate jithendar-com
kubectl get ingressclass,storageclass
```

- **Missing Traefik:** run `deploy-traefik.sh`, then `expose-ingress.sh` on Vultr; an IngressClass object alone cannot route traffic.
- **PVC Pending:** check the default StorageClass and Minikube storage-provisioner addon; inspect PVC events.
- **Certificate Pending:** inspect Challenge events; verify DNS (including any AAAA record), inbound port 80 and the issuer's Traefik HTTP-01 solver.
- **Timeout from the browser:** check Vultr and host firewalls, and host port 443 forwarding to Traefik. Kubeconfig API access is separate from website access.
- **404 or broken assets:** ensure the `/grafana` Prefix ingress uses Traefik and no proxy strips the prefix. Keep `root_url=https://jithendar.com/grafana/` and `serve_from_sub_path=true` together.
- **502/503:** inspect Grafana readiness, logs and Service endpoints.
- **No metrics:** configure a reachable Prometheus data source; Grafana does not collect metrics itself.

Configuration follows [Grafana's reverse proxy guidance](https://grafana.com/tutorials/run-grafana-behind-a-proxy/) and the [Grafana Helm installation guide](https://grafana.com/docs/grafana/latest/setup-grafana/installation/helm/).

For existing installations in `roboshop-ai`, see [namespace migration notes](../README.md).

**TLS ownership:** Grafana and Prometheus have no Certificate of their own. Traefik serves the shared `jithendar/jithendar-com` certificate (Secret `jithendar-com-tls`) to every route through the default TLSStore owned by the `shared-tls` Application. See [observability](../README.md#access).
