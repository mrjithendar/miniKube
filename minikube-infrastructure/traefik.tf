# Traefik, the ingress controller for every public route on mk4 (jithendar.com, /argocd, /grafana, /kiali, ...).
# Values: deploy/minikube/traefik/values.yaml. NodePorts 30080/30443; the host forwards 80/443 to them
# (deploy/minikube/expose-ingress.sh), so a values change here restarts the only public entry point.
resource "helm_release" "traefik" {
  name             = "traefik"
  namespace        = "traefik"
  create_namespace = true
  repository       = "https://traefik.github.io/charts"
  chart            = "traefik"
  version          = "42.0.0"
  values           = [file("${local.minikube}/traefik/values.yaml")]

  wait    = true
  timeout = var.helm_timeout
}

# mk4's release "traefik" was installed by bootstrap-cluster.sh with Helm before Terraform owned it. Importing adopts
# that release (no reinstall, no downtime beyond the upgrade to the Git values). Once it is in state this block does
# nothing. On a fresh cluster there is nothing to import: set adopt_existing_traefik = false.
import {
  for_each = var.adopt_existing_traefik ? toset(["traefik/traefik"]) : toset([])
  to       = helm_release.traefik
  id       = each.value
}
