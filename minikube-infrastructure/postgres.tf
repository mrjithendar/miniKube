# PostgreSQL on mk4: the CloudNativePG operator and ONE shared 3-instance cluster (primary + 2 replicas, one per node,
# local-path volumes) in namespace postgres-db for every application, plus its public access
# (https://jithendar.com/postgres/ pgAdmin 4, psql at postgres.jithendar.com:443, both behind an IP allowlist).
# README.md, "PostgreSQL".
#
# Tenancy: each entry of var.postgres_databases gets a database and a login role of the same name that owns it (no
# role can read another application's database), a random password kept in this (encrypted) state, the role's Secret
# in postgres-db (CloudNativePG reads it) and a connection Secret in the application's own namespace:
#   <namespace>/postgres-db-credentials: host, port, dbname, username, password, uri (through PgBouncer),
#   direct-host, direct-uri (straight to the primary; for migrations that need session features PgBouncer lacks).
# Removing an entry drops the role's login and the Secrets but keeps the database (databaseReclaimPolicy retain).

locals {
  postgres_namespace = "postgres-db"
  postgres_cluster   = "postgres-cluster"
  postgres_rw_host   = "${local.postgres_cluster}-rw.${local.postgres_namespace}.svc.cluster.local"
  postgres_pool_host = "${local.postgres_cluster}-pooler-rw.${local.postgres_namespace}.svc.cluster.local"

  # Merged over deploy/minikube/postgres/cluster.yaml (maps merge, these lists are only set here).
  postgres_tenants = {
    cluster = {
      roles = [for name, db in var.postgres_databases : {
        name            = name
        ensure          = "present"
        login           = true
        inherit         = true
        connectionLimit = db.connection_limit
        passwordSecret  = { name = "db-${name}" }
      }]
    }
    databases = [for name, db in var.postgres_databases : {
      name                  = name
      owner                 = name
      ensure                = "present"
      databaseReclaimPolicy = "retain"
    }]
  }
}

# Not injected into the mesh: CloudNativePG runs its own TLS and replication between instances, and Istio sidecars
# break its init jobs. Meshed clients reach it in plain text (auto mTLS sees no sidecar).
resource "kubernetes_namespace_v1" "postgres_db" {
  metadata {
    name = local.postgres_namespace
    labels = {
      "app.kubernetes.io/part-of"          = "postgres-db"
      "pod-security.kubernetes.io/enforce" = "baseline"
      "pod-security.kubernetes.io/warn"    = "restricted"
    }
  }
}

# Operator: CRDs, webhooks, controller. Watches every namespace.
resource "helm_release" "cnpg_operator" {
  name             = "cnpg"
  namespace        = "cnpg-system"
  create_namespace = true
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cloudnative-pg"
  version          = "0.29.1"
  values           = [file("${local.minikube}/postgres/operator.yaml")]

  wait    = true
  timeout = var.helm_timeout
}

resource "random_password" "postgres" {
  for_each = var.postgres_databases
  length   = 32
  special  = false
}

# The role's password, where CloudNativePG reads it (managed roles; cnpg.io/reload re-applies a changed password).
resource "kubernetes_secret_v1" "postgres_role" {
  for_each = var.postgres_databases
  metadata {
    name      = "db-${each.key}"
    namespace = kubernetes_namespace_v1.postgres_db.metadata[0].name
    labels = {
      "cnpg.io/reload"            = "true"
      "app.kubernetes.io/part-of" = "postgres-db"
    }
  }
  type = "kubernetes.io/basic-auth"
  data = {
    username = each.key
    password = random_password.postgres[each.key].result
  }
}

# What the application mounts. Its namespace must exist before this apply.
resource "kubernetes_secret_v1" "postgres_app" {
  for_each = var.postgres_databases
  metadata {
    name      = "postgres-db-credentials"
    namespace = each.value.namespace
    labels = {
      "app.kubernetes.io/part-of"    = "postgres-db"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  data = {
    host        = local.postgres_pool_host
    port        = "5432"
    dbname      = each.key
    username    = each.key
    password    = random_password.postgres[each.key].result
    uri         = "postgresql://${each.key}:${random_password.postgres[each.key].result}@${local.postgres_pool_host}:5432/${each.key}"
    direct-host = local.postgres_rw_host
    direct-uri  = "postgresql://${each.key}:${random_password.postgres[each.key].result}@${local.postgres_rw_host}:5432/${each.key}"
  }
}

# The Cluster, its Pooler, PodDisruptionBudget, and one Database object per application. Helm only waits for the
# objects to be accepted; the operator then runs initdb on the first instance and clones the replicas from it (a few
# minutes on first install):
#   kubectl --context mk4 -n postgres-db get cluster,database,pooler
resource "helm_release" "postgres_cluster" {
  name       = local.postgres_cluster
  namespace  = kubernetes_namespace_v1.postgres_db.metadata[0].name
  repository = "https://cloudnative-pg.github.io/charts"
  chart      = "cluster"
  version    = "0.9.0"
  values = [
    file("${local.minikube}/postgres/cluster.yaml"),
    yamlencode(local.postgres_tenants),
  ]

  wait    = true
  timeout = var.helm_timeout

  # The operator's webhooks must answer before a Cluster can be created; the volumes need the local-path class; the
  # roles' Secrets should exist before the operator reconciles them.
  depends_on = [helm_release.cnpg_operator, helm_release.local_path_provisioner, kubernetes_secret_v1.postgres_role]
}

# pgAdmin's own login at https://jithendar.com/postgres/ (the chart's ui.adminSecret):
#   kubectl --context mk4 -n postgres-db get secret postgres-ui-admin -o jsonpath='{.data.password}' | base64 -d
# pgAdmin stores it in its database on first start only: changing it here later does not change the login (change it
# in pgAdmin, or delete PVC postgres-ui-data to start over).
resource "random_password" "postgres_ui_admin" {
  length  = 24
  special = false
}

resource "kubernetes_secret_v1" "postgres_ui_admin" {
  metadata {
    name      = "postgres-ui-admin"
    namespace = kubernetes_namespace_v1.postgres_db.metadata[0].name
    labels = {
      "app.kubernetes.io/part-of"    = "postgres-db"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
  data = {
    email    = var.postgres_ui_admin_email
    password = random_password.postgres_ui_admin.result
  }
}

# pgAdmin, the TCP route, the gateway allowlists, NetworkPolicies and the metrics Service (local chart).
resource "helm_release" "postgres_access" {
  name      = "postgres-access"
  namespace = kubernetes_namespace_v1.postgres_db.metadata[0].name
  chart     = "${local.minikube}/postgres/access"
  values = [
    file("${local.minikube}/postgres/access/values.yaml"),
    yamlencode({ allowedIps = var.postgres_allowed_ips }),
    local.chart_checksum["postgres/access"],
  ]

  wait    = true
  timeout = var.helm_timeout

  lifecycle {
    precondition {
      condition     = length(var.postgres_allowed_ips) > 0
      error_message = "postgres_allowed_ips is empty: list the client addresses (CIDRs) allowed to reach /postgres and postgres.jithendar.com."
    }
  }

  # The gateway's TLS server and /postgres route live in the edge chart.
  depends_on = [helm_release.postgres_cluster, helm_release.istio_edge, kubernetes_secret_v1.postgres_ui_admin]
}
