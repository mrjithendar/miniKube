# OPA Gatekeeper: admission policies and a periodic audit of what is already running.
# Values: deploy/minikube/gatekeeper/values.yaml; policies: the gatekeeper/library and gatekeeper/constraints charts
# (deploy/minikube/gatekeeper/README.md).
resource "helm_release" "gatekeeper" {
  name             = "gatekeeper"
  namespace        = "gatekeeper-system"
  create_namespace = true
  repository       = "https://open-policy-agent.github.io/gatekeeper/charts"
  chart            = "gatekeeper"
  version          = "3.23.1"
  values           = [file("${local.minikube}/gatekeeper/values.yaml")]

  wait    = true
  timeout = var.helm_timeout
}

# ConstraintTemplates, vendored from gatekeeper-library. Each one makes Gatekeeper create a constraint kind (CRD).
resource "helm_release" "gatekeeper_library" {
  name      = "gatekeeper-library"
  namespace = "gatekeeper-system"
  chart     = "${local.minikube}/gatekeeper/library"
  values    = [local.chart_checksum["gatekeeper/library"]]

  wait    = true
  timeout = var.helm_timeout

  # ConstraintTemplate is a Gatekeeper CRD.
  depends_on = [helm_release.gatekeeper]
}

# Gatekeeper creates a template's constraint CRD a few seconds after the template. Helm cannot wait for that, so a
# constraint installed straight after its template fails with "no matches for kind". Waits again whenever the library
# changes (a new template is a new kind).
resource "time_sleep" "gatekeeper_constraint_kinds" {
  create_duration = "30s"
  triggers = {
    library = helm_release.gatekeeper_library.metadata.revision
  }
}

# What is enforced where: deploy/minikube/gatekeeper/constraints/values.yaml.
resource "helm_release" "gatekeeper_constraints" {
  name      = "gatekeeper-constraints"
  namespace = "gatekeeper-system"
  chart     = "${local.minikube}/gatekeeper/constraints"
  values    = [file("${local.minikube}/gatekeeper/constraints/values.yaml"), local.chart_checksum["gatekeeper/constraints"]]

  wait    = true
  timeout = var.helm_timeout

  depends_on = [time_sleep.gatekeeper_constraint_kinds]
}
