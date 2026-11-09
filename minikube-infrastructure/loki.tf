# Loki and Alloy. Public: the read endpoints under https://jithendar.com/loki/api/v1/ (Secret loki-read-auth) and
# /loki/api/v1/push (Secret loki-push-auth); nothing else under /loki. Local chart deploy/minikube/observability/loki.
resource "helm_release" "loki" {
  name             = "loki"
  namespace        = "monitoring"
  create_namespace = true
  chart            = "${local.minikube}/observability/loki"
  values           = [local.chart_checksum["observability/loki"]]

  # The live objects were applied by the retired Argo CD Application (Kustomize), without a Helm release.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout

  # Routed by the Istio gateway (delegate VirtualService in the chart; login in istio-edge).
  depends_on = [helm_release.istio_base]
}
