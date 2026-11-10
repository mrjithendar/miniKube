# Harbor, the private OCI registry: https://registry.jithendar.com (portal at /harbor/; https://jithendar.com/harbor
# redirects there, edge chart). Two releases in namespace harbor:
#   harbor-access  local chart deploy/minikube/harbor/access: token and public certificates, the registry.jithendar.com
#                  VirtualService, the registry's mTLS exception, the (suspended) R2 backup;
#   harbor         chart harbor/harbor, values deploy/minikube/harbor/values.yaml (arm64 images built by deploy-harbor.sh).
# Database: the shared PostgreSQL (postgres_databases "registry"); Redis: the shared Valkey (redis_tenants "harbor").
# Both write their connection Secret into namespace harbor, so these tenants must stay in vars/mk4.tfvars.
#
# Prerequisites, created outside Terraform (deploy/minikube/harbor/README.md); this root only checks that they exist:
#   harbor/harbor-credentials  ./create-secrets.sh --services harbor --apply (also creates the namespace)
#   harbor/dockerhub-pull      bash deploy/minikube/harbor/deploy-harbor.sh --skip-build

locals {
  harbor_namespace = "harbor"
}

# The namespace's Secrets, to check the prerequisites exist (preconditions below). Their contents are read into this
# (encrypted) state like every other password here; the namespace and both Secrets stay create-secrets.sh's and
# deploy-harbor.sh's.
data "kubernetes_resources" "harbor_prerequisites" {
  api_version    = "v1"
  kind           = "Secret"
  namespace      = local.harbor_namespace
  field_selector = "type!=helm.sh/release.v1"
}

locals {
  harbor_secrets = [for s in data.kubernetes_resources.harbor_prerequisites.objects : s.metadata.name]
}

# In the mesh: every Harbor pod gets an Istio sidecar (STRICT mTLS, the mesh's AuthorizationPolicy for harbor, Valkey's
# enforced ALLOW policy). The namespace stays create-secrets.sh's; Terraform owns only this label, and installs nothing
# in harbor before it is set, so no pod starts without a sidecar.
resource "kubernetes_labels" "harbor_namespace" {
  api_version = "v1"
  kind        = "Namespace"
  metadata {
    name = local.harbor_namespace
  }
  labels = {
    "istio-injection" = "enabled"
  }
  # The label already comes from deploy/minikube/namespaces/harbor.yaml (kubectl apply): take it over.
  force = true

  depends_on = [helm_release.istiod]
}

resource "helm_release" "harbor_access" {
  name      = "harbor-access"
  namespace = local.harbor_namespace
  chart     = "${local.minikube}/harbor/access"
  values = [
    file("${local.minikube}/harbor/access/values.yaml"),
    local.chart_checksum["harbor/access"],
  ]

  wait    = true
  timeout = var.helm_timeout

  lifecycle {
    precondition {
      condition     = contains(local.harbor_secrets, "harbor-credentials")
      error_message = "Secret harbor/harbor-credentials is missing: run ./create-secrets.sh --services harbor --apply first."
    }
  }

  # Certificates: cert-manager and ClusterIssuer letsencrypt-dns. VirtualService: the gateway's https host
  # harbor/registry.jithendar.com (edge chart).
  depends_on = [kubernetes_labels.harbor_namespace, helm_release.letsencrypt, helm_release.istio_edge]
}

resource "helm_release" "harbor" {
  name       = "harbor"
  namespace  = local.harbor_namespace
  repository = "https://helm.goharbor.io"
  chart      = "harbor"
  version    = "1.19.4"
  values     = [file("${local.minikube}/harbor/values.yaml")]

  wait    = true
  timeout = var.helm_timeout

  lifecycle {
    precondition {
      condition     = contains(local.harbor_secrets, "dockerhub-pull")
      error_message = "Secret harbor/dockerhub-pull is missing (the arm64 images are private on Docker Hub): run bash deploy/minikube/harbor/deploy-harbor.sh --skip-build first."
    }
  }

  # Core mounts harbor-token-tls and harbor-public-tls (harbor-access); its database and Redis Secrets and network
  # access come from the shared PostgreSQL and Valkey releases.
  depends_on = [
    kubernetes_labels.harbor_namespace, helm_release.harbor_access,
    helm_release.postgres_access, kubernetes_secret_v1.postgres_app,
    helm_release.redis_access, kubernetes_secret_v1.redis_app,
  ]
}
