# Traefik, being retired: the Istio ingress gateway (istio.tf, istio-edge) is mk4's edge. While traefik_enabled is true
# it keeps running, still holds the host's ports 30080/30443 until expose-ingress.sh points the host at the gateway,
# and hands everything it has no route for to the gateway. Set traefik_enabled = false (vars/mk4.tfvars) after the
# host forwards to the gateway: this uninstalls Traefik and makes the gateway insist on the PROXY protocol. Values:
# deploy/minikube/traefik/values.yaml.
resource "helm_release" "traefik" {
  count = var.traefik_enabled ? 1 : 0

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

# Same release, now behind count: no reinstall.
moved {
  from = helm_release.traefik
  to   = helm_release.traefik[0]
}

# mk4's release "traefik" was installed by bootstrap-cluster.sh with Helm before Terraform owned it. Importing adopts
# that release. Once it is in state this block does nothing. Never on a fresh cluster (adopt_existing_traefik false).
import {
  for_each = var.adopt_existing_traefik && var.traefik_enabled ? toset(["traefik/traefik"]) : toset([])
  to       = helm_release.traefik[0]
  id       = each.value
}
