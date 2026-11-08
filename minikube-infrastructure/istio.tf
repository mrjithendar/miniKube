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

# mk4's public edge, namespace istio-ingress, local chart deploy/minikube/istio/edge: Gateway public, its wildcard
# certificate, the jithendar.com routing table, logins, read-only rules, PROXY protocol, compression, and while Traefik
# runs its hand-off routes. Applications bind delegate VirtualServices to the routing table.
#
# The logins (htpasswd lines) come from Secret istio-ingress/gateway-basic-auth, written by
# scripts/istio/gateway-basic-auth.py from the applications' own login Secrets. They are in this state file and in the
# EnvoyFilter as {SHA} hashes, never in Git.
data "kubernetes_secret_v1" "gateway_basic_auth" {
  metadata {
    name      = "gateway-basic-auth"
    namespace = "istio-ingress"
  }
}

locals {
  edge_values      = yamldecode(file("${local.minikube}/istio/edge/values.yaml"))
  basic_auth_names = [for login in local.edge_values.basicAuth : login.name]
}

resource "helm_release" "istio_edge" {
  name      = "istio-edge"
  namespace = "istio-ingress"
  chart     = "${local.minikube}/istio/edge"

  values = [
    file("${local.minikube}/istio/edge/values.yaml"),
    yamlencode({
      traefikHandoff = { enabled = var.traefik_enabled }
      proxyProtocol  = { requireHeader = !var.traefik_enabled }
    }),
  ]
  set_sensitive = [for name in local.basic_auth_names : {
    name  = "basicAuthUsers.${name}"
    value = lookup(data.kubernetes_secret_v1.gateway_basic_auth.data, name, "")
    type  = "string"
  }]

  wait    = true
  timeout = var.helm_timeout

  lifecycle {
    precondition {
      condition = alltrue([for name in local.basic_auth_names :
      lookup(nonsensitive(data.kubernetes_secret_v1.gateway_basic_auth.data), name, "") != ""])
      error_message = "Secret istio-ingress/gateway-basic-auth lacks a login: run python3 scripts/istio/gateway-basic-auth.py first."
    }
  }

  # Gateway/EnvoyFilter/AuthorizationPolicy (istio-base CRDs, a running gateway), Certificate (cert-manager and
  # ClusterIssuer letsencrypt-dns), and while Traefik runs, IngressRoute/ServersTransport (Traefik's CRDs).
  depends_on = [helm_release.istio_ingressgateway, helm_release.letsencrypt, helm_release.traefik]
}
