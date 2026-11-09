# Prometheus at https://jithendar.com/prometheus (basic auth, Secret monitoring/monitoring-auth).
# Local chart deploy/minikube/observability/prometheus.
resource "helm_release" "prometheus" {
  name             = "prometheus"
  namespace        = "monitoring"
  create_namespace = true
  chart            = "${local.minikube}/observability/prometheus"
  values           = [local.chart_checksum["observability/prometheus"]]

  # The live objects were rendered by the retired Argo CD Application (same release name), without a Helm release.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout

  # Routed by the Istio gateway (delegate VirtualService in the chart; login in istio-edge).
  depends_on = [helm_release.istio_base]
}
