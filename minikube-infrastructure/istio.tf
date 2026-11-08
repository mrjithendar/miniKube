# Istio service mesh: CRDs (base) and control plane (istiod), one version for both. No ingress gateway: Traefik stays
# the edge. Sidecars are injected only into namespaces labelled istio-injection=enabled (none yet).
resource "helm_release" "istio_base" {
  name             = "istio-base"
  namespace        = "istio-system"
  create_namespace = true
  repository       = "https://istio-release.storage.googleapis.com/charts"
  chart            = "base"
  version          = "1.30.7"

  wait    = true
  timeout = var.helm_timeout
}

resource "helm_release" "istiod" {
  name       = "istiod"
  namespace  = "istio-system"
  repository = "https://istio-release.storage.googleapis.com/charts"
  chart      = "istiod"
  version    = "1.30.7"
  values     = [file("${local.minikube}/istio/istiod.yaml")]

  wait    = true
  timeout = var.helm_timeout

  depends_on = [helm_release.istio_base]
}
