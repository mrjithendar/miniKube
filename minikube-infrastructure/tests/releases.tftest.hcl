# Mock providers only: no cluster is contacted. Run: terraform init -backend=false && terraform test
mock_provider "helm" {}
mock_provider "kubernetes" {}
mock_provider "random" {}

run "pinned_releases" {
  command = plan

  assert {
    condition = (
      helm_release.metrics_server.version == "3.14.0" &&
      helm_release.cert_manager.version == "v1.20.4" &&
      helm_release.istio_base.version == helm_release.istiod.version &&
      helm_release.istiod.version == "1.30.7" &&
      helm_release.kiali.version == "2.34.0" &&
      helm_release.grafana.version == "14.0.1"
    )
    error_message = "Chart versions are pinned; istio-base and istiod must stay on one version."
  }

  assert {
    condition = (
      helm_release.metrics_server.namespace == "kube-system" &&
      helm_release.cert_manager.namespace == "cert-manager" &&
      helm_release.letsencrypt.namespace == "cert-manager" &&
      helm_release.istiod.namespace == "istio-system" &&
      helm_release.kiali.namespace == "istio-system" &&
      helm_release.prometheus.namespace == "monitoring" &&
      helm_release.grafana.namespace == "monitoring" &&
      helm_release.loki.namespace == "monitoring" &&
      helm_release.otel_collector.namespace == "observability" &&
      helm_release.jaeger.namespace == "observability"
    )
    error_message = "Release namespaces must match the live installations."
  }

  assert {
    condition = alltrue([for r in [
      helm_release.metrics_server, helm_release.cert_manager, helm_release.prometheus, helm_release.grafana,
      helm_release.grafana_dashboards, helm_release.loki, helm_release.otel_collector, helm_release.jaeger,
    ] : r.take_ownership])
    error_message = "These objects were applied by Argo CD without a Helm release; the first apply must adopt them."
  }

  assert {
    condition     = random_password.kiali_signing_key.length == 32 && !random_password.kiali_signing_key.special
    error_message = "Kiali signing keys must be 16, 24 or 32 characters."
  }
}

run "missing_jithendar_dashboards_fail" {
  command = plan

  variables {
    jithendar_com_dir = "no-such-checkout"
  }

  expect_failures = [helm_release.grafana_dashboards]
}
