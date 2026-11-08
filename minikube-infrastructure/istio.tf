# Istio service mesh: CRDs (base), the CNI node agent, the control plane (istiod) and an ingress gateway, one version
# for all four, plus the gateway's public routing (istio-edge). Traefik still receives every connection from the host and
# hands jithendar.com/www to the gateway (deploy/minikube/istio/edge/values.yaml has the migration steps). Sidecars are
# injected only into namespaces labelled istio-injection=enabled.
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

# Istio ingress gateway (Envoy, injected from istiod's "gateway" template) in its own namespace. Values:
# deploy/minikube/istio/ingressgateway.yaml. NodePort 31080/31443; Traefik keeps 30080/30443 from the host.
resource "helm_release" "istio_ingressgateway" {
  name             = "istio-ingressgateway"
  namespace        = "istio-ingress"
  create_namespace = true
  repository       = "https://istio-release.storage.googleapis.com/charts"
  chart            = "gateway"
  version          = "1.30.7"
  values           = [file("${local.minikube}/istio/ingressgateway.yaml")]

  wait    = true
  timeout = var.helm_timeout

  # The gateway pod is filled in by istiod's injection webhook; without istiod it starts with an empty proxy image.
  depends_on = [helm_release.istiod]
}

# Gateways, their Let's Encrypt certificates (DNS-01), response compression and the temporary Traefik hand-off routes, in
# namespace istio-ingress. Local chart deploy/minikube/istio/edge. Applications bind VirtualServices to these Gateways.
resource "helm_release" "istio_edge" {
  name      = "istio-edge"
  namespace = "istio-ingress"
  chart     = "${local.minikube}/istio/edge"

  wait    = true
  timeout = var.helm_timeout

  # Gateway/EnvoyFilter (istio-base CRDs, a running gateway), Certificate (cert-manager CRDs and ClusterIssuer
  # letsencrypt-dns), IngressRoute/ServersTransport (Traefik CRDs).
  depends_on = [helm_release.istio_ingressgateway, helm_release.letsencrypt, helm_release.traefik]
}
