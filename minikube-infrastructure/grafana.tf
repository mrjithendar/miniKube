# Grafana at https://jithendar.com/grafana (admin login from Secret monitoring/monitoring-auth).
# Values: deploy/minikube/observability/grafana/values.yaml.

locals {
  jithendar_dashboards_dir = abspath("${path.module}/${var.jithendar_com_dir}/grafana")
  jithendar_dashboards = {
    for name in fileset(local.jithendar_dashboards_dir, "*.json") : name => file("${local.jithendar_dashboards_dir}/${name}")
  }
}

# ConfigMaps grafana-dashboards-{roboshop,moks} (deploy/minikube/observability/grafana/dashboards) and
# grafana-dashboards-jithendar (the jithendar.com checkout's grafana/ folder). Grafana mounts all three.
resource "helm_release" "grafana_dashboards" {
  name      = "grafana-dashboards"
  namespace = "monitoring"
  chart     = "${local.minikube}/observability/grafana/dashboards"
  values = [yamlencode({
    jithendar = local.jithendar_dashboards
  })]

  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout

  lifecycle {
    precondition {
      condition     = length(local.jithendar_dashboards) > 0
      error_message = "No dashboards in ${local.jithendar_dashboards_dir}: clone jithendar.com there or set jithendar_com_dir."
    }
  }

  depends_on = [helm_release.prometheus]
}

resource "helm_release" "grafana" {
  name             = "grafana"
  namespace        = "monitoring"
  create_namespace = true
  repository       = "https://grafana-community.github.io/helm-charts"
  chart            = "grafana"
  version          = "14.0.1"
  values           = [file("${local.minikube}/observability/grafana/values.yaml")]

  # The live objects were rendered by the retired Argo CD Application (same release name), without a Helm release.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout

  depends_on = [helm_release.grafana_dashboards]
}
