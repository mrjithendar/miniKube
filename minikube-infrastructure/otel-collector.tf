# OpenTelemetry Collector (OTLP in, traces to Jaeger), plus the ExternalName Service roboshop-ai/otel-collector.
# Local chart deploy/minikube/observability/otel-collector.
resource "helm_release" "otel_collector" {
  name             = "otel-collector"
  namespace        = "observability"
  create_namespace = true
  chart            = "${local.minikube}/observability/otel-collector"

  # The live objects were applied by the retired Argo CD Application (Kustomize), without a Helm release.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout

  depends_on = [helm_release.jaeger]
}
