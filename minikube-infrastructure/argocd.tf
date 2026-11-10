# Argo CD at https://jithendar.com/argocd (release argocd, namespace argocd, chart argo-cd). Values:
# deploy/minikube/argocd/values.yaml, which also renders the /argocd VirtualService and the sidecar NetworkPolicy.
#
# No Redis of its own: Argo CD's cache is on the shared Valkey (redis.tf) as the built-in tenant "argocd" (ACL user
# argocd, database 7 only, no admin command). Valkey is a Terraform release, never an Argo CD Application, so the GitOps
# controller does not depend on something it deploys itself.
#
# Already installed by scripts/argocd/deploy.py? Adopt it once instead of reinstalling (README.md, Argo CD):
#   terraform import -var-file=vars/mk4.tfvars kubernetes_namespace_v1.argocd argocd
#   terraform import -var-file=vars/mk4.tfvars helm_release.argocd argocd/argocd

locals {
  argocd_namespace = "argocd"
  # Merged into the Valkey tenants (redis.tf, local.redis_tenants). values.yaml's configs.params "redis.db" must match.
  argocd_redis  = { namespace = local.argocd_namespace, databases = [7] }
  argocd_values = yamldecode(file("${local.minikube}/argocd/values.yaml"))
}

# In the mesh: Valkey accepts only mTLS (STRICT), so every Argo CD pod needs a sidecar.
resource "kubernetes_namespace_v1" "argocd" {
  metadata {
    name = local.argocd_namespace
    labels = {
      "app.kubernetes.io/part-of" = "argocd"
      "istio-injection"           = "enabled"
    }
  }
}

# What the chart's externalRedis.existingSecret reads (keys fixed by the chart).
resource "kubernetes_secret_v1" "argocd_redis" {
  metadata {
    name      = local.argocd_values.externalRedis.existingSecret
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of"    = "argocd"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  data = {
    redis-username = "argocd"
    redis-password = random_password.redis["argocd"].result
  }

  lifecycle {
    precondition {
      condition     = local.argocd_values.configs.params["redis.db"] == tostring(local.argocd_redis.databases[0])
      error_message = "configs.params redis.db in deploy/minikube/argocd/values.yaml must be ${local.argocd_redis.databases[0]}, Argo CD's database on Valkey (local.argocd_redis)."
    }
    precondition {
      condition     = local.argocd_values.externalRedis.host == local.redis_host
      error_message = "externalRedis.host in deploy/minikube/argocd/values.yaml must be ${local.redis_host}."
    }
  }
}

resource "helm_release" "argocd" {
  name       = "argocd"
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = "10.10.5"
  values = [
    file("${local.minikube}/argocd/values.yaml"),
    # The pods read the password at start: a new one (tainted random_password) must roll them.
    yamlencode({ global = { podAnnotations = { "checksum/redis" = sha1(random_password.redis["argocd"].result) } } }),
  ]

  # The VirtualService and NetworkPolicy were applied with kubectl (kustomize) before this release rendered them.
  take_ownership = true
  wait           = true
  timeout        = var.helm_timeout

  # Sidecar injection (istiod); ACL user argocd loaded into Valkey (redis) and namespace argocd admitted by its
  # AuthorizationPolicy and NetworkPolicy (redis_access); the /argocd delegate in the routing table (istio_edge).
  depends_on = [
    helm_release.istiod, helm_release.redis, helm_release.redis_access, helm_release.istio_edge,
    kubernetes_secret_v1.argocd_redis,
  ]
}
