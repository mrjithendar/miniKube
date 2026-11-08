# Metrics Server for kubectl top and HPAs. Values: deploy/minikube/metrics-server/values.yaml.
resource "helm_release" "metrics_server" {
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = "3.14.0"
  values     = [file("${local.minikube}/metrics-server/values.yaml")]

  # The live objects were applied by the retired Argo CD Application, without a Helm release.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout
}
