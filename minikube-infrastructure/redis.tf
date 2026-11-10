# Redis on mk4: ONE shared, highly available Valkey (Redis-compatible) in namespace redis for every application
# (replacing each application's own Redis pod), in the Istio mesh with STRICT mTLS, plus its public access
# (https://jithendar.com/redis/ RedisInsight, redis-cli at redis.jithendar.com:443, both behind an IP allowlist).
# README.md, "Redis (Valkey)".
#
# Tenancy: each entry of var.redis_tenants, and Argo CD (argocd.tf), gets an ACL user of the same name that can open
# only its own database numbers (Valkey 9 ACL "db=") and no admin command (CONFIG, FLUSHALL, SHUTDOWN, REPLICAOF, ACL,
# MONITOR, ...), a random password kept in this (encrypted) state, and a connection Secret in the application's own
# namespace:
#   <namespace>/redis-<name>-credentials: host, port, username, password, db (its first database), databases, uri.
# The users are "user" lines (password as SHA-256) in valkey.conf. Adding, removing or re-keying one rolls the
# StatefulSet; each primary hands over to a replica first (Sentinel failover in preStop), so writes pause for about a
# second. Removing an entry deletes the user and its Secret; its keys stay until flushed by hand.

locals {
  redis_namespace = "redis"
  redis_release   = "valkey"
  redis_host      = "${local.redis_release}-master.${local.redis_namespace}.svc.cluster.local"
  redis_values    = yamldecode(file("${local.minikube}/redis/values.yaml"))

  # Every ACL user: var.redis_tenants plus Argo CD (argocd.tf), which has a Secret of its own in the chart's format.
  redis_tenants = merge(var.redis_tenants, { argocd = local.argocd_redis })

  # Commands no tenant may run: the @admin category and the ones that reach other tenants' databases. FLUSHDB stays:
  # it is limited to the tenant's own database.
  redis_tenant_acl = { for name, t in local.redis_tenants : name =>
    "~* &* +@all -@admin -flushall -swapdb db=${join(",", t.databases)}"
  }
  # RedisInsight (access chart): every database, read and write, no admin command and no flush.
  redis_console_acl = "~* &* +@all -@admin -flushall -flushdb -swapdb alldbs"

  redis_acl_lines = concat(
    ["user console on #${sha256(random_password.redis_console.result)} ${local.redis_console_acl}"],
    [for name, acl in local.redis_tenant_acl : "user ${name} on #${sha256(random_password.redis[name].result)} ${acl}"],
  )

  # Merged over deploy/minikube/redis/values.yaml. A list replaces, it does not merge: the file's extraConfig first.
  redis_overrides = {
    config = { extraConfig = concat(local.redis_values.config.extraConfig, local.redis_acl_lines) }
    # The chart rolls nothing when its ConfigMap changes: this does, so new or changed users are loaded.
    podAnnotations = { "checksum/acl" = sha1(join("\n", local.redis_acl_lines)) }
  }
}

# In the mesh: every pod gets an Istio sidecar (native sidecar, started before the init container asks Sentinel for the
# primary). STRICT mTLS and the enforced AuthorizationPolicy are in the access chart.
resource "kubernetes_namespace_v1" "redis" {
  metadata {
    name = local.redis_namespace
    labels = {
      "app.kubernetes.io/part-of"          = "redis"
      "istio-injection"                    = "enabled"
      "pod-security.kubernetes.io/enforce" = "baseline"
      "pod-security.kubernetes.io/warn"    = "restricted"
    }
  }
}

resource "random_password" "redis_admin" {
  length  = 32
  special = false
}

resource "random_password" "redis_console" {
  length  = 32
  special = false
}

resource "random_password" "redis" {
  for_each = local.redis_tenants
  length   = 32
  special  = false
}

# "default" is the admin user (chart: requirepass, replication, Sentinel, HAProxy checks, exporter); "console" is
# RedisInsight's; one key per tenant for reference (the server itself has their hashes).
resource "kubernetes_secret_v1" "redis_users" {
  metadata {
    name      = "valkey-users"
    namespace = kubernetes_namespace_v1.redis.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of" = "redis"
    }
  }
  data = merge(
    { default = random_password.redis_admin.result, console = random_password.redis_console.result },
    { for name, _ in local.redis_tenants : name => random_password.redis[name].result },
  )
}

# What the application mounts. Its namespace must exist before this apply. Not for Argo CD (argocd.tf writes its own).
resource "kubernetes_secret_v1" "redis_app" {
  for_each = var.redis_tenants
  metadata {
    name      = "redis-${each.key}-credentials"
    namespace = each.value.namespace
    labels = {
      "app.kubernetes.io/part-of"    = "redis"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  data = {
    host      = local.redis_host
    port      = "6379"
    username  = each.key
    password  = random_password.redis[each.key].result
    db        = tostring(each.value.databases[0])
    databases = join(",", each.value.databases)
    uri       = "redis://${each.key}:${random_password.redis[each.key].result}@${local.redis_host}:6379/${each.value.databases[0]}"
  }
}

# The gateway login for https://jithendar.com/redis/ (edge chart basicAuth "redis"; scripts/istio/gateway-basic-auth.py
# turns it into the gateway's htpasswd line) and RedisInsight's key for the passwords it stores.
resource "random_password" "redis_ui" {
  length  = 32
  special = false
}

resource "random_password" "redis_ui_encryption" {
  length  = 48
  special = false
}

resource "kubernetes_secret_v1" "redis_ui_auth" {
  metadata {
    name      = "redis-ui-auth"
    namespace = kubernetes_namespace_v1.redis.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of" = "redis"
    }
  }
  type = "kubernetes.io/basic-auth"
  data = {
    username       = "redis"
    password       = random_password.redis_ui.result
    encryption-key = random_password.redis_ui_encryption.result
  }
}

# StatefulSet valkey (3 pods: Valkey, Sentinel, HAProxy, exporter, Istio proxy), Services, PodDisruptionBudget.
# The chart pulls from Docker Hub's OCI registry; a local docker credsStore that is not installed breaks the pull
# (README.md, Commands: DOCKER_CONFIG).
resource "helm_release" "redis" {
  name       = local.redis_release
  namespace  = kubernetes_namespace_v1.redis.metadata[0].name
  repository = "oci://registry-1.docker.io/cloudpirates"
  chart      = "valkey"
  version    = "0.25.16"
  values = [
    file("${local.minikube}/redis/values.yaml"),
    yamlencode(local.redis_overrides),
  ]

  # The chart's HAProxy closes a connection after 30 s without traffic, which would cut idle Pub/Sub subscribers and
  # blocking reads (BRPOP queues). The post-renderer adds "timeout tunnel 1h" (Envoy's own TCP idle timeout) and
  # fails if the chart's HAProxy config no longer has the line it expects.
  postrender = {
    binary_path = "${path.module}/../deploy/minikube/redis/postrender-haproxy.sh"
  }

  wait    = true
  timeout = var.helm_timeout

  depends_on = [helm_release.local_path_provisioner, helm_release.istiod, kubernetes_secret_v1.redis_users]
}

# PeerAuthentication STRICT, AuthorizationPolicies, NetworkPolicies, the metrics Service, RedisInsight, the TCP route
# and the gateway allowlists (local chart).
resource "helm_release" "redis_access" {
  name      = "redis-access"
  namespace = kubernetes_namespace_v1.redis.metadata[0].name
  chart     = "${local.minikube}/redis/access"
  values = [
    file("${local.minikube}/redis/access/values.yaml"),
    yamlencode({
      allowedIps       = var.redis_allowed_ips
      clientNamespaces = sort(distinct([for t in values(local.redis_tenants) : t.namespace]))
    }),
    local.chart_checksum["redis/access"],
  ]

  wait    = true
  timeout = var.helm_timeout

  lifecycle {
    precondition {
      condition     = length(var.redis_allowed_ips) > 0
      error_message = "redis_allowed_ips is empty: list the client addresses (CIDRs) allowed to reach /redis and redis.jithendar.com."
    }
  }

  # RedisInsight reads valkey-users and redis-ui-auth; the gateway's TLS server and /redis route live in the edge chart.
  depends_on = [helm_release.redis, kubernetes_secret_v1.redis_ui_auth, helm_release.istio_edge]
}
