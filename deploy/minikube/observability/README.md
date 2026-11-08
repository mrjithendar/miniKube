# Observability

All observability workloads are Terraform Helm releases ([`minikube-infrastructure/`](../../../minikube-infrastructure/README.md))
built from this directory. Terraform applies the local checkout, not `master`.

| Signal | Component | Helm release | Namespace | Source |
| --- | --- | --- | --- | --- |
| Metrics | Prometheus 3.15 | `prometheus` | `monitoring` | [`prometheus/`](prometheus/README.md) (local Helm chart) |
| Dashboards | Grafana (community chart 14.0.1) | `grafana` | `monitoring` | [`grafana/values.yaml`](grafana/README.md) |
| Logs | Loki 3.7.9 + Alloy 1.21.2 | `loki` | `monitoring` | [`loki/`](loki/README.md) (local Helm chart) |
| Traces | OpenTelemetry Collector 0.162 → Jaeger 2.22 | `otel-collector`, `jaeger` | `observability` | [`otel-collector/`](otel-collector/), [`jaeger/`](jaeger/) (local Helm charts, `jaeger.yaml`), [design notes](opentelemetry/README.md) |
| Mesh | Kiali 2.34 on Istio 1.30.7 | `kiali`, `istiod`, `istio-base` | `istio-system` | [`../istio/`](../istio/) |

```bash
terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
```

## How the pieces connect

- **Prometheus** scrapes itself, kubelet/cAdvisor on every node, the RoboShop
  services' `/metrics` endpoints (job `roboshop`), Loki and Alloy. Alert rules in
  `prometheus/files/alerts.yml` are evaluated and shown under **Alerts**. No
  Alertmanager is installed, so alerts are not delivered anywhere.
- **Alloy** tails pod logs from `roboshop-ai`, `traefik` and `observability`
  through the Kubernetes API, keeps only sanitized `event`/`level`/`trace_id`
  fields, and pushes to Loki.
- **Grafana** has two provisioned datasources: Prometheus (default) and Loki.
- **Traces** go from services over OTLP/HTTP to
  `otel-collector.observability.svc.cluster.local:4318` (set in `roboshop-config`),
  which strips sensitive attributes and forwards to Jaeger. The collector's only
  configuration is [`otel-collector/collector.yaml`](otel-collector/collector.yaml);
  its checksum is a pod annotation, so editing it rolls the collector.
  An `ExternalName` Service `roboshop-ai/otel-collector` keeps pods that still use
  the short name `otel-collector:4318` working; remove it once every RoboShop pod
  has restarted with the new endpoint. Jaeger storage is in-memory (`jaeger/jaeger.yaml`, newest 5000 traces),
  with no ingress. Both expose their own metrics on :8888 (Prometheus jobs `otel-collector`, `jaeger`).

## Access

- Grafana: **https://jithendar.com/grafana/**
- Prometheus: **https://jithendar.com/prometheus/** (Traefik BasicAuth)
- Loki: read-only API under **https://jithendar.com/loki/api/v1/** (Traefik BasicAuth,
  [`loki-read-auth`](loki/README.md#remote-read-moks-on-eks)), for MOKS on EKS, and
  push from EKS at `/loki/api/v1/push` with its own login,
  [`loki-push-auth`](loki/README.md#remote-push-logs-from-eks). Use Grafana Explore for logs.
- Jaeger: cluster-internal only. Use
  `kubectl -n roboshop-ai port-forward svc/jaeger 16686` for the UI.

TLS comes from the shared `jithendar/jithendar-com` Certificate, served to every
route through Traefik's default TLSStore (`shared-tls` Application). The monitoring
ingresses do not request their own certificates.

## Shared login Secret

Grafana and Prometheus share **`monitoring/monitoring-auth`** (type
`kubernetes.io/basic-auth`, keys `username`/`password`, username `admin`).
`./create-secrets.sh` creates it through [`credentials.py`](credentials.py),
reusing an existing Secret without rotating it. Grafana reads it as its initial
admin credential; Prometheus's Traefik Middleware uses it for BasicAuth.

```bash
kubectl --context mk4 -n monitoring get secret monitoring-auth \
  -o jsonpath='{.data.password}' | base64 --decode; echo
```

Grafana also stores the admin password in its database. After rotating the
Secret, run `python3 deploy/minikube/observability/credentials.py sync-grafana --context mk4`
to update user ID 1 (the password is passed over stdin, never as an argument).

## Verify

```bash
kubectl --context mk4 -n monitoring get pods,pvc
helm --kube-context mk4 list -n monitoring
python3 -m pytest -q tests/test_loki.py tests/test_yaml_layout.py deploy/minikube/test-monitoring-credentials.py
```

In Prometheus, **Status → Target health** should show every target up. In
Grafana, **Explore → Loki** with `{namespace="roboshop-ai"}` should return lines.

## Data

Prometheus (5Gi, 7 days), Grafana (2Gi), Loki (10Gi, 7 days) and Alloy (1Gi
read positions) use PVCs that are kept when an Application is removed. Jaeger
traces are lost on restart. This is a single-node lab setup, not HA.

## Runbooks

Alert rules link here (`runbook_url`). jithendar.com's own alerts link to its `www/docs/OBSERVABILITY.md`.

- **ObservabilityTargetDown** — `kubectl --context mk4 -n monitoring get pods` (Loki, Alloy) or `-n observability`
  (collector, Jaeger). A Jaeger restart empties the trace store; logs and metrics are not affected.
- **LogShippingDropped** — Alloy gave up on batches after its retries; those lines are gone. `kubectl -n monitoring
  logs deploy/alloy | grep "dropping data"` shows Loki's answer (on 2026-11-02 it was `500 Ingester is shutting
  down`); then `kubectl -n monitoring logs deploy/loki`. Restarting Loki is the usual fix; pod logs are still on
  the nodes for a while, so `kubectl logs` can recover recent lines.
- **TraceSpansLost** — `otelcol_receiver_refused_spans` means the collector's memory limiter refused data
  (limit 128 MiB in `collector.yaml`); `otelcol_exporter_send_failed_spans` means Jaeger did not accept the batch
  (Jaeger down or restarting). Applications are not affected: they export in the background and drop spans.
- **JaegerMemoryNearLimit** — lower `max_traces` in `jaeger/jaeger.yaml` or raise the memory limit, then
  push and sync the `jaeger` Application. Check first that one service is not flooding it:
  `curl localhost:16686/api/v3/services` and recent operations per service.
