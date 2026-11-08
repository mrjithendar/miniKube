# Kiali at https://jithendar.com/kiali/ (token sign-in, view only). Values: deploy/minikube/istio/kiali.yaml.

# Signs Kiali's login cookies; Kiali accepts 16, 24 or 32 characters. Kept in the (encrypted) state so every apply
# reuses it.
resource "random_password" "kiali_signing_key" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "kiali_signing_key" {
  metadata {
    name      = "kiali-signing-key"
    namespace = "istio-system"
  }
  data = {
    key = random_password.kiali_signing_key.result
  }

  # istio-base creates the namespace.
  depends_on = [helm_release.istio_base]
}

resource "helm_release" "kiali" {
  name       = "kiali"
  namespace  = "istio-system"
  repository = "https://kiali.org/helm-charts"
  chart      = "kiali-server"
  version    = "2.34.0"
  values     = [file("${local.minikube}/istio/kiali.yaml")]

  wait    = true
  timeout = var.helm_timeout

  depends_on = [helm_release.istiod, helm_release.prometheus, kubernetes_secret_v1.kiali_signing_key]
}
