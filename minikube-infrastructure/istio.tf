# Istio service mesh: CRDs (base), the CNI node agent and the control plane (istiod), one version for all three. No
# ingress gateway: Traefik stays the edge. Sidecars are injected only into namespaces labelled istio-injection=enabled
# (moks, jithendar).
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

# The CNI agent sets up each meshed pod's traffic redirection on the node, so injected pods carry no istio-init
# container with NET_ADMIN and NET_RAW. Without it Pod Security "baseline" (namespace moks) rejects every injected pod.
# Chart defaults match minikube's kindnet: /etc/cni/net.d and /opt/cni/bin, chained.
resource "helm_release" "istio_cni" {
  name       = "istio-cni"
  namespace  = "istio-system"
  repository = "https://istio-release.storage.googleapis.com/charts"
  chart      = "cni"
  version    = "1.30.7"

  wait    = true
  timeout = var.helm_timeout

  depends_on = [helm_release.istio_base]
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

  depends_on = [helm_release.istio_base, helm_release.istio_cni]
}
