# Istio service mesh: CRDs (base), the CNI node agent, the control plane (istiod) and an ingress gateway, one version
# for all four, plus the gateway's public routing (istio-edge) and the mesh-wide policies (istio-mesh). The host's
# HAProxy sends every public connection to the gateway. Sidecars are injected only into namespaces labelled
# istio-injection=enabled.
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
# Chained to k3s's flannel; k3s's CNI paths are in deploy/minikube/istio/cni.yaml.
resource "helm_release" "istio_cni" {
  name       = "istio-cni"
  namespace  = "istio-system"
  repository = "https://istio-release.storage.googleapis.com/charts"
  chart      = "cni"
  version    = "1.30.7"
  values     = [file("${local.minikube}/istio/cni.yaml")]

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
# deploy/minikube/istio/ingressgateway.yaml. Service type LoadBalancer: Hetzner's cloud controller creates an LB11
# (PROXY protocol) in front of it; jithendar.com's DNS points at that load balancer.
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
# certificate, the jithendar.com routing table, logins, read-only rules, PROXY protocol, compression and response
# headers. Applications bind delegate VirtualServices to the routing table.
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
  # A missing Secret reads as data = null; treat it as empty so the precondition below reports it.
  gateway_basic_auth = data.kubernetes_secret_v1.gateway_basic_auth.data == null ? {} : data.kubernetes_secret_v1.gateway_basic_auth.data
}

resource "helm_release" "istio_edge" {
  name      = "istio-edge"
  namespace = "istio-ingress"
  chart     = "${local.minikube}/istio/edge"

  values = [file("${local.minikube}/istio/edge/values.yaml"), local.chart_checksum["istio/edge"]]
  set_sensitive = [for name in local.basic_auth_names : {
    name  = "basicAuthUsers.${name}"
    value = lookup(local.gateway_basic_auth, name, "")
    type  = "string"
  }]

  wait    = true
  timeout = var.helm_timeout

  lifecycle {
    precondition {
      condition = alltrue([for name in local.basic_auth_names :
      nonsensitive(lookup(local.gateway_basic_auth, name, "") != "")])
      error_message = "Secret istio-ingress/gateway-basic-auth lacks a login: run python3 scripts/istio/gateway-basic-auth.py first."
    }
  }

  # Gateway/EnvoyFilter/AuthorizationPolicy (istio-base CRDs, a running gateway), Certificate (cert-manager and
  # ClusterIssuer letsencrypt-dns).
  depends_on = [helm_release.istio_ingressgateway, helm_release.letsencrypt]
}

# Mesh-wide policies, chart deploy/minikube/istio/mesh: STRICT mTLS for every namespace without its own
# PeerAuthentication, AuthorizationPolicies for the platform namespaces (dry run until the report is clean), and the
# gateway's access log. After Kiali, which must be meshed before Prometheus turns STRICT.
resource "helm_release" "istio_mesh" {
  name      = "istio-mesh"
  namespace = "istio-system"
  chart     = "${local.minikube}/istio/mesh"
  values    = [file("${local.minikube}/istio/mesh/values.yaml"), local.chart_checksum["istio/mesh"]]

  wait    = true
  timeout = var.helm_timeout

  depends_on = [helm_release.istiod, helm_release.istio_ingressgateway, helm_release.kiali]
}
