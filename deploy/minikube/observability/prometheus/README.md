> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. This component is a Terraform Helm release:
> `minikube-infrastructure/README.md`. Historical examples below are not
> runnable deployment instructions.

# Prometheus at https://jithendar.com/prometheus/

From the repository root, with Traefik, public host routing and cert-manager already installed:

```bash
bash deploy/minikube/deploy-prometheus.sh
bash deploy/minikube/deploy-grafana.sh
```

The second command updates Grafana’s data source and synchronizes its administrator password with `monitoring-auth`, preserving its volume. The installer follows the selected Minikube profile; use `KUBE_CONTEXT=YOUR_CONTEXT` on either command when working through a remote kubeconfig. Requires Helm, kubectl, Python 3, a default StorageClass and the Traefik Middleware CRDs. For a fresh cluster follow [Traefik setup](../../traefik/README.md) first. DNS and public TCP 80/443 must reach the Vultr ingress controller.

## Login

Open **https://jithendar.com/prometheus/**. Username: **admin**. Retrieve the generated password:

```bash
kubectl -n monitoring get secret monitoring-auth \
  -o jsonpath='{.data.password}' | base64 --decode; echo
```

Add `--context=YOUR_CONTEXT` if necessary. This is the same password used by Grafana. The installers reuse the shared Secret on reruns and never print the password itself.

## Deployment

- Namespace `monitoring`, release and Service `prometheus`.
- Official image `prom/prometheus:v3.15.0`, one replica, 5 GiB PVC, 7-day/4 GB retention limits. The size limit applies to TSDB blocks, so the volume includes extra headroom for WAL/head data; monitor disk usage.
- Requests 100m CPU/256 MiB RAM; limits 1 CPU/768 MiB RAM. Higher scrape volume may require more resources.
- HTTPS ingress with `/prometheus` Prefix matching, the shared `jithendar/jithendar-com-tls` certificate (default TLSStore), and Traefik BasicAuth. No prefix stripping. Grafana's `/grafana` and WordPress's `/` routes remain separate.
- Both `--web.external-url` and `--web.route-prefix` include `/prometheus`, including health checks and self-scraping. Admin/lifecycle APIs are not enabled.
- Public authentication is enforced at Traefik. Grafana uses `http://prometheus.monitoring.svc.cluster.local:9090/prometheus` directly. Cluster-internal access is not authenticated by this setup. The authentication Secret uses Kubernetes' `kubernetes.io/basic-auth` format; anyone authorized to read that Secret can read its password.

Use HTTPS directly; this chart does not create an HTTP redirect. The deployment uses a single persistent instance suitable for the Minikube sandbox. Helm keeps the PVC on uninstall; deleting the namespace/cluster can still destroy metrics.

## Metrics and Grafana

Prometheus scrapes itself plus **kubelet and cAdvisor on every discovered node**. Check **Status → Target health** or query `up`. `kubernetesMetrics.enabled` controls node discovery, the service account and its RBAC. Scraping uses the Kubernetes API proxy with the service-account token and cluster CA verification. The service account can list/watch nodes and GET their proxy endpoints; it does not read Secrets.

Metrics Server remains separate: it supplies `kubectl top`, not Prometheus storage. cAdvisor supplies pod/container CPU and memory (including WordPress/MySQL/Redis containers). This chart does not install node-exporter or kube-state-metrics, so imported dashboards requiring `node_*` or `kube_*` series can still be empty. Application request counts, database queries and cache-hit metrics require application instrumentation or dedicated exporters.

Add actual cluster-reachable application/exporter endpoints under `extraScrapeConfigs` in `values.yaml`, then rerun the installer. For example, only if that Service and metrics endpoint exist:

```yaml
extraScrapeConfigs:
  - job_name: roboshop
    metrics_path: /metrics
    static_configs:
      - targets:
          - ai-gateway.roboshop-ai.svc.cluster.local:3000
```

After rerunning `deploy-grafana.sh`, Grafana has a provisioned default **Prometheus** data source (`roboshop-prometheus`). Use Explore and query `up`. Provisioning does not remove other manually added data sources; resolve any pre-existing conflicting default/name before provisioning. No dashboards are imported automatically.

## Troubleshooting

```bash
kubectl -n monitoring get pods,pvc,svc,ingress
kubectl -n monitoring logs deployment/prometheus --tail=100
kubectl -n monitoring describe deployment prometheus
kubectl -n monitoring get middleware prometheus-auth
kubectl -n jithendar describe certificate jithendar-com
curl -I https://jithendar.com/prometheus/
# Authenticated test prompts for the password instead of putting it in shell history:
curl -u admin https://jithendar.com/prometheus/-/ready
```

An unauthenticated HTTP **401** is expected from the public endpoint. A **404** suggests missing ingress/path configuration; **503** usually means the pod is not ready. For a timeout or untrusted certificate follow the [cert-manager](../../cert-manager/README.md) and [Traefik](../../traefik/README.md) guides. PVC Pending means storage provisioning/capacity needs attention. Grafana connection failures often mean the data source omitted `/prometheus` or the server is not running.

Reference: [Prometheus URL flags](https://prometheus.io/docs/prometheus/latest/command-line/prometheus/), [Traefik BasicAuth](https://doc.traefik.io/traefik/v3.4/middlewares/http/basicauth/).

For existing installations in `roboshop-ai`, see [namespace migration notes](../README.md).

**TLS ownership:** Grafana and Prometheus have no Certificate of their own. Traefik serves the shared `jithendar/jithendar-com` certificate (Secret `jithendar-com-tls`) to every route through the default TLSStore owned by the `shared-tls` Application. See [observability](../README.md#access).

### Installer appears stuck after the certificate message

`certificate ... unchanged` means the certificate manifest was applied successfully. Older versions then ran a silent Helm `--wait` for up to 10 minutes. The updated script prints each phase, shows pod/PVC status, uses `kubectl rollout status`, and prints storage/pod events plus current/previous logs when deployment fails.

While an existing install is waiting, inspect it from another terminal (use its printed context):

```bash
kubectl --context=mk4 -n monitoring get pods,pvc
kubectl --context=mk4 -n monitoring describe pods -l app=roboshop-prometheus
kubectl --context=mk4 -n monitoring describe pvc prometheus-data
kubectl --context=mk4 -n monitoring logs deployment/prometheus --tail=80
```

`Pending` requires checking scheduling/storage events; `ImagePullBackOff` requires checking image download events; `CrashLoopBackOff` requires current/previous container logs. Do not guess which condition occurred from the certificate message alone. Let an active Helm operation finish before starting another upgrade. Use `bash deploy/minikube/deploy-prometheus.sh` for the next run; the updated script also handles invocation through `sh`.

### Permission denied opening `/prometheus-data/queries.active`

This means Prometheus cannot write to the mounted data volume. The chart runs Prometheus as UID/GID **65534**. Its `data-permissions` init container sets the mounted PVC's ownership to that UID/GID and grants owner read/write/directory traversal permissions before starting Prometheus. This accommodates Minikube storage that does not apply `fsGroup` ownership. Existing metrics files are preserved; do not delete the PVC.

With the updated files, rerun:

```bash
bash deploy/minikube/deploy-prometheus.sh
kubectl -n monitoring rollout status deployment/prometheus --timeout=10m
kubectl -n monitoring logs deployment/prometheus -c prometheus --tail=50
```

If the init container itself fails:

```bash
kubectl -n monitoring logs deployment/prometheus -c data-permissions
kubectl -n monitoring describe pods -l app=roboshop-prometheus
```

Only this init container runs as root, with a read-only root filesystem and the capabilities needed to repair the data mount. The main server remains non-root. On storage that already supplies correct permissions, or a cluster policy that disallows root init containers, set `volumePermissions.enabled: false` and arrange ownership through your storage administrator/driver instead. Recursive permission repair may take time on a large existing volume.

### Useful queries

Select **Table** to inspect current values, or **Graph** for history. New CPU rate series need at least two samples (about a minute at the 30-second scrape interval).

```promql
up
```

Container CPU cores used per pod:

```promql
sum by (namespace, pod) (
  rate(container_cpu_usage_seconds_total{job="kubernetes-cadvisor",container!="",container!="POD",pod!=""}[5m])
)
```

Container working-set memory in MiB per pod:

```promql
sum by (namespace, pod) (
  container_memory_working_set_bytes{job="kubernetes-cadvisor",container!="",container!="POD",pod!=""}
) / 1024 / 1024
```

These queries also work in Grafana Explore with the Prometheus data source. If `up` is zero for a target, open Target health and inspect its last scrape error. If `up` is one but a specific dashboard has no data, check the metric names and label filters it expects.

## Metrics from EKS

The nodes of the `roboshop-demo` EKS cluster are in private subnets, so this Prometheus cannot scrape them. A Prometheus agent there (tools repository: `deploy/eks/applications/roboshop-demo-prometheus-agent.yaml`) scrapes node-exporter and pushes to `https://jithendar.com/prometheus/api/v1/write`. Those series have `cluster="roboshop-demo"` and `job="eks-node-exporter"`.

That one path has its own Traefik login, `prometheus-remote-write-auth`, so EKS never holds the shared `monitoring-auth` password. The login only reaches the write endpoint. Create the same password on both clusters, before syncing this chart and applying the Terraform:

```bash
PASS=$(openssl rand -hex 32)
kubectl --context mk4 -n monitoring create secret generic prometheus-remote-write-auth \
  --type=kubernetes.io/basic-auth --from-literal=username=eks --from-literal=password="$PASS"
kubectl --context roboshop-demo create namespace monitoring
kubectl --context roboshop-demo -n monitoring create secret generic prometheus-remote-write-auth \
  --from-literal=password="$PASS"
unset PASS
```
