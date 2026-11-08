# Let's Encrypt DNS-01 ClusterIssuers letsencrypt-dns (+ -staging) and Certificate jithendar/jithendar-com-wildcard
# for jithendar.com and *.jithendar.com. Local chart deploy/minikube/cert-manager/issuers.
# Needs Secret cert-manager/cloudflare-api-token first (README.md); without it the Certificate stays pending.
resource "helm_release" "letsencrypt" {
  name      = "letsencrypt-jithendar"
  namespace = "cert-manager"
  chart     = "${local.minikube}/cert-manager/issuers"

  wait    = true
  timeout = var.helm_timeout

  # ClusterIssuer and Certificate are cert-manager CRDs.
  depends_on = [helm_release.cert_manager]
}
