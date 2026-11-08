# Jaeger 2.x, in-memory storage, cluster-internal only (Grafana's Jaeger data source; UI by port-forward).
# Local chart deploy/minikube/observability/jaeger.
resource "helm_release" "jaeger" {
  name             = "jaeger"
  namespace        = "observability"
  create_namespace = true
  chart            = "${local.minikube}/observability/jaeger"

  # The live objects were applied by the retired Argo CD Application (Kustomize), without a Helm release.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout
}
