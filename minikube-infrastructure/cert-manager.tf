# cert-manager with its CRDs. Values: deploy/minikube/cert-manager/values.yaml.
resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = true
  repository       = "oci://quay.io/jetstack/charts"
  chart            = "cert-manager"
  version          = "v1.20.4"
  values           = [file("${local.minikube}/cert-manager/values.yaml")]

  # The live objects were applied by an earlier Argo CD Application, without a Helm release.
  # The chart keeps its CRDs on uninstall (crds.keep), so Certificates survive a reinstall.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout
}
