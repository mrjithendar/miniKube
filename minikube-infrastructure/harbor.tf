# Harbor, the private OCI registry: https://registry.jithendar.com (portal at /harbor/; https://jithendar.com/harbor
# redirects there, edge chart). Two releases in namespace harbor:
#   harbor-access  local chart deploy/minikube/harbor/access: token and public certificates, the registry.jithendar.com
#                  VirtualService, the registry's mTLS exception, the (suspended) R2 backup;
#   harbor         chart harbor/harbor, values deploy/minikube/harbor/values.yaml (arm64 images built by deploy-harbor.sh).
# Database: the shared PostgreSQL (postgres_databases "registry"); Redis: the shared Valkey (redis_tenants "harbor").
# Both write their connection Secret into namespace harbor, so these tenants must stay in vars/mk4.tfvars.
#
# Everything Harbor needs is here; no script runs first:
#   namespace harbor (in the mesh), Secret harbor-credentials (generated once, never rotated: secretKey decrypts the
#   registry credentials Harbor stores), Secret dockerhub-pull (the private arm64 images, from var.dockerhub_username
#   and var.dockerhub_password: repository secrets DOCKER_USERNAME and DOCKER_PASSWORD in CI).
# Images: built and pushed by deploy/minikube/harbor/deploy-harbor.sh --skip-deploy or the "Build Harbor images" workflow.

#MAC
# export TF_VAR_dockerhub_username=jithendar
# export TF_VAR_dockerhub_password=PAT

# bash scripts/harbor-adopt-into-terraform.sh
# DOCKER_CONFIG=$(mktemp -d) terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars


locals {
  harbor_namespace = "harbor"
  # harbor-credentials: the chart key each value backs (values.yaml) and its length. secretKey must be exactly 16
  # (Harbor's AES key); the admin password must have an upper-case letter, a lower-case letter and a digit.
  harbor_credential_lengths = {
    HARBOR_ADMIN_PASSWORD = 24
    secretKey             = 16
    secret                = 16
    CSRF_KEY              = 32
    JOBSERVICE_SECRET     = 16
    REGISTRY_HTTP_SECRET  = 16
    REGISTRY_PASSWD       = 32
  }
  harbor_registry_user = "harbor_registry_user" # registry.credentials.username in values.yaml
}

# In the mesh: every Harbor pod gets an Istio sidecar (STRICT mTLS, the mesh's AuthorizationPolicy for harbor, Valkey's
# enforced ALLOW policy). Both releases depend on it, so no pod starts before injection is on.
resource "kubernetes_namespace_v1" "harbor" {
  metadata {
    name = local.harbor_namespace
    labels = {
      "app.kubernetes.io/part-of" = "harbor"
      "istio-injection"           = "enabled"
    }
  }
  depends_on = [helm_release.istiod]
}

resource "random_password" "harbor" {
  for_each    = local.harbor_credential_lengths
  length      = each.value
  special     = false
  min_upper   = each.key == "HARBOR_ADMIN_PASSWORD" ? 1 : 0
  min_lower   = each.key == "HARBOR_ADMIN_PASSWORD" ? 1 : 0
  min_numeric = each.key == "HARBOR_ADMIN_PASSWORD" ? 1 : 0

  # Never rotated: Harbor stores the admin password in its database on first start, and secretKey decrypts what it
  # stores. This also keeps values imported from a Secret created before Terraform owned it (README.md, Harbor).
  lifecycle {
    ignore_changes = all
  }
}

# Every credential the chart would otherwise generate at render time (and change on every upgrade).
resource "kubernetes_secret_v1" "harbor_credentials" {
  metadata {
    name      = "harbor-credentials"
    namespace = kubernetes_namespace_v1.harbor.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of"    = "harbor"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  data = merge(
    { for key, _ in local.harbor_credential_lengths : key => random_password.harbor[key].result },
    # The registry's htpasswd line; random_password's bcrypt hash is stable across plans.
    { REGISTRY_HTPASSWD = "${local.harbor_registry_user}:${random_password.harbor["REGISTRY_PASSWD"].bcrypt_hash}" },
  )
}

# imagePullSecrets in values.yaml: docker.io/jithendar/custom-harbour is private.
resource "kubernetes_secret_v1" "harbor_dockerhub_pull" {
  metadata {
    name      = "dockerhub-pull"
    namespace = kubernetes_namespace_v1.harbor.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of"    = "harbor"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  type = "kubernetes.io/dockerconfigjson"
  data = {
    ".dockerconfigjson" = jsonencode({ auths = { "https://index.docker.io/v1/" = {
      auth = base64encode("${var.dockerhub_username}:${var.dockerhub_password}")
    } } })
  }

  lifecycle {
    precondition {
      condition     = var.dockerhub_username != "" && nonsensitive(var.dockerhub_password != "")
      error_message = "dockerhub_username and dockerhub_password are empty: export TF_VAR_dockerhub_username and TF_VAR_dockerhub_password (CI: repository secrets DOCKER_USERNAME, DOCKER_PASSWORD). A read-only Docker Hub access token is enough."
    }
  }
}

resource "helm_release" "harbor_access" {
  name      = "harbor-access"
  namespace = kubernetes_namespace_v1.harbor.metadata[0].name
  chart     = "${local.minikube}/harbor/access"
  values = [
    file("${local.minikube}/harbor/access/values.yaml"),
    local.chart_checksum["harbor/access"],
  ]

  wait    = true
  timeout = var.helm_timeout

  # Certificates: cert-manager and ClusterIssuer letsencrypt-dns. VirtualService: the gateway's https host
  # harbor/registry.jithendar.com (edge chart).
  depends_on = [helm_release.letsencrypt, helm_release.istio_edge]
}

resource "helm_release" "harbor" {
  name       = "harbor"
  namespace  = kubernetes_namespace_v1.harbor.metadata[0].name
  repository = "https://helm.goharbor.io"
  chart      = "harbor"
  version    = "1.19.4"
  values     = [file("${local.minikube}/harbor/values.yaml")]

  wait    = true
  timeout = var.helm_timeout

  # Core mounts harbor-token-tls and harbor-public-tls (harbor-access); its database and Redis Secrets and network
  # access come from the shared PostgreSQL and Valkey releases.
  depends_on = [
    kubernetes_secret_v1.harbor_credentials, kubernetes_secret_v1.harbor_dockerhub_pull, helm_release.harbor_access,
    helm_release.postgres_access, kubernetes_secret_v1.postgres_app,
    helm_release.redis_access, kubernetes_secret_v1.redis_app,
  ]
}
