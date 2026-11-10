# Mock providers only: no cluster is contacted. Run: terraform init -backend=false && terraform test
mock_provider "helm" {}
mock_provider "kubernetes" {
  # The gateway's logins (scripts/istio/gateway-basic-auth.py writes them on mk4).
  override_data {
    target = data.kubernetes_secret_v1.gateway_basic_auth
    values = {
      data = {
        "prometheus-remote-write" = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
        "prometheus"              = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
        "loki-push"               = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
        "loki-read"               = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
        "opensearch-dashboards"   = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
        "opensearch"              = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
        "redis"                   = "a:{SHA}AAAAAAAAAAAAAAAAAAAAAAAAAAA="
      }
    }
  }
  # Harbor's prerequisites (create-secrets.sh, deploy-harbor.sh).
  override_data {
    target = data.kubernetes_resources.harbor_prerequisites
    values = {
      objects = [{ metadata = { name = "harbor-credentials" } }, { metadata = { name = "dockerhub-pull" } }]
    }
  }
}
mock_provider "random" {}

# Every run: the access charts refuse to plan without an allowlist (runs below override these to test that).
variables {
  postgres_allowed_ips = ["192.0.2.1/32"]
  redis_allowed_ips    = ["192.0.2.1/32"]
}

run "pinned_releases" {
  command = plan

  assert {
    condition = (
      helm_release.metrics_server.version == "3.14.0" &&
      helm_release.cert_manager.version == "v1.20.4" &&
      helm_release.istio_base.version == helm_release.istiod.version &&
      helm_release.istiod.version == "1.30.7" &&
      helm_release.istio_cni.version == helm_release.istiod.version &&
      helm_release.istio_ingressgateway.version == helm_release.istiod.version &&
      helm_release.kiali.version == "2.34.0" &&
      helm_release.grafana.version == "14.0.1"
    )
    error_message = "Chart versions are pinned; istio-base, istio-cni, istiod and the ingress gateway must stay on one version."
  }

  assert {
    condition = (
      helm_release.metrics_server.namespace == "kube-system" &&
      helm_release.cert_manager.namespace == "cert-manager" &&
      helm_release.letsencrypt.namespace == "cert-manager" &&
      helm_release.istiod.namespace == "istio-system" &&
      helm_release.istio_cni.namespace == "istio-system" &&
      helm_release.istio_ingressgateway.namespace == "istio-ingress" &&
      helm_release.istio_edge.namespace == helm_release.istio_ingressgateway.namespace &&
      helm_release.istio_mesh.namespace == "istio-system" &&
      helm_release.kiali.namespace == "istio-system" &&
      helm_release.prometheus.namespace == "monitoring" &&
      helm_release.grafana.namespace == "monitoring" &&
      helm_release.loki.namespace == "monitoring" &&
      helm_release.otel_collector.namespace == "observability" &&
      helm_release.jaeger.namespace == "observability"
    )
    error_message = "Release namespaces must match the live installations."
  }

  assert {
    condition = alltrue([for r in [
      helm_release.metrics_server, helm_release.cert_manager, helm_release.prometheus, helm_release.grafana,
      helm_release.grafana_dashboards, helm_release.loki, helm_release.otel_collector, helm_release.jaeger,
    ] : r.take_ownership])
    error_message = "These objects were applied by Argo CD without a Helm release; the first apply must adopt them."
  }

  assert {
    condition     = random_password.kiali_signing_key.length == 32 && !random_password.kiali_signing_key.special
    error_message = "Kiali signing keys must be 16, 24 or 32 characters."
  }
}

run "missing_jithendar_dashboards_fail" {
  command = plan

  variables {
    jithendar_com_dir = "no-such-checkout"
  }

  expect_failures = [helm_release.grafana_dashboards]
}

run "postgres_releases" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
  }

  assert {
    condition = (
      helm_release.cnpg_operator.version == "0.29.1" &&
      helm_release.postgres_cluster.version == "0.9.0" &&
      helm_release.cnpg_operator.namespace == "cnpg-system" &&
      helm_release.postgres_cluster.namespace == "postgres-db" &&
      helm_release.postgres_access.namespace == helm_release.postgres_cluster.namespace &&
      helm_release.local_path_provisioner.namespace == "local-path-storage"
    )
    error_message = "CloudNativePG chart versions are pinned; the cluster and its access chart share namespace postgres-db."
  }
}

run "postgres_without_allowlist_fails" {
  command = plan

  variables {
    postgres_allowed_ips = []
  }

  expect_failures = [helm_release.postgres_access]
}

run "postgres_tenants_get_a_database_role_and_credentials" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    postgres_databases = {
      shop = { namespace = "shop-ns" }
      blog = { namespace = "blog-ns", connection_limit = 10 }
    }
  }

  assert {
    condition = (
      kubernetes_secret_v1.postgres_role["shop"].metadata[0].namespace == "postgres-db" &&
      kubernetes_secret_v1.postgres_role["shop"].metadata[0].name == "db-shop" &&
      kubernetes_secret_v1.postgres_role["shop"].type == "kubernetes.io/basic-auth" &&
      kubernetes_secret_v1.postgres_app["blog"].metadata[0].namespace == "blog-ns" &&
      kubernetes_secret_v1.postgres_app["blog"].metadata[0].name == "postgres-db-credentials" &&
      kubernetes_secret_v1.postgres_app["blog"].data.host == "postgres-cluster-pooler-rw.postgres-db.svc.cluster.local" &&
      kubernetes_secret_v1.postgres_app["blog"].data.dbname == "blog"
    )
    error_message = "Each application gets db-<name> in postgres-db and postgres-db-credentials (through PgBouncer) in its namespace."
  }

  assert {
    condition = (
      [for r in local.postgres_tenants.cluster.roles : r.connectionLimit if r.name == "blog"][0] == 10 &&
      [for r in local.postgres_tenants.cluster.roles : r.connectionLimit if r.name == "shop"][0] == 50 &&
      alltrue([for d in local.postgres_tenants.databases : d.owner == d.name && d.databaseReclaimPolicy == "retain"])
    )
    error_message = "Each database is owned by its own role and kept when its entry is removed."
  }
}

run "postgres_reserved_names_refused" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    postgres_databases   = { postgres = { namespace = "x" } }
  }

  expect_failures = [var.postgres_databases]
}

run "redis_releases" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = ["192.0.2.1/32"]
  }

  assert {
    condition = (
      helm_release.redis.version == "0.25.16" &&
      helm_release.redis.repository == "oci://registry-1.docker.io/cloudpirates" &&
      helm_release.redis.namespace == "redis" &&
      helm_release.redis_access.namespace == helm_release.redis.namespace &&
      kubernetes_namespace_v1.redis.metadata[0].labels["istio-injection"] == "enabled"
    )
    error_message = "The Valkey chart is pinned; it and its access chart share namespace redis, which is in the mesh."
  }
}

run "redis_without_allowlist_fails" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = []
  }

  expect_failures = [helm_release.redis_access]
}

run "redis_tenants_get_a_scoped_user_and_credentials" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = ["192.0.2.1/32"]
    redis_tenants = {
      shop = { namespace = "shop-ns", databases = [3] }
      blog = { namespace = "blog-ns", databases = [4, 5] }
    }
  }

  assert {
    condition = (
      kubernetes_secret_v1.redis_app["blog"].metadata[0].namespace == "blog-ns" &&
      kubernetes_secret_v1.redis_app["blog"].metadata[0].name == "redis-blog-credentials" &&
      kubernetes_secret_v1.redis_app["blog"].data.host == "valkey-master.redis.svc.cluster.local" &&
      kubernetes_secret_v1.redis_app["blog"].data.db == "4" &&
      kubernetes_secret_v1.redis_app["blog"].data.databases == "4,5"
    )
    error_message = "Each tenant gets redis-<name>-credentials in its namespace, pointing at the master proxy and its first database."
  }

  assert {
    condition = (
      local.redis_tenant_acl["blog"] == "~* &* +@all -@admin -flushall -swapdb db=4,5" &&
      local.redis_tenant_acl["shop"] == "~* &* +@all -@admin -flushall -swapdb db=3" &&
      length(local.redis_acl_lines) == 4 &&
      length(local.redis_overrides.config.extraConfig) == length(local.redis_values.config.extraConfig) + 4
    )
    error_message = "Each tenant's user is limited to its own databases and has no admin command; the file's config lines are kept."
  }
}

run "redis_shared_database_refused" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = ["192.0.2.1/32"]
    redis_tenants = {
      a = { namespace = "x", databases = [1] }
      b = { namespace = "y", databases = [1, 2] }
    }
  }

  expect_failures = [var.redis_tenants]
}

run "argocd_uses_the_shared_valkey" {
  command = plan

  assert {
    condition = (
      helm_release.argocd.version == "10.10.5" &&
      helm_release.argocd.namespace == "argocd" &&
      kubernetes_namespace_v1.argocd.metadata[0].labels["istio-injection"] == "enabled" &&
      local.argocd_values.redis.enabled == false &&
      local.argocd_values["redis-ha"].enabled == false &&
      local.argocd_values.externalRedis.host == local.redis_host &&
      kubernetes_secret_v1.argocd_redis.metadata[0].namespace == "argocd" &&
      kubernetes_secret_v1.argocd_redis.data["redis-username"] == "argocd"
    )
    error_message = "Argo CD runs no Redis of its own; it signs in to valkey-master as ACL user argocd, from the mesh."
  }

  assert {
    condition = (
      local.redis_tenant_acl["argocd"] == "~* &* +@all -@admin -flushall -swapdb db=7" &&
      local.argocd_values.configs.params["redis.db"] == "7" &&
      contains(yamldecode(helm_release.redis_access.values[1]).clientNamespaces, "argocd") &&
      !contains(keys(kubernetes_secret_v1.redis_app), "argocd")
    )
    error_message = "ACL user argocd is limited to database 7 and namespace argocd may reach Valkey."
  }
}

run "redis_argocd_database_refused" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = ["192.0.2.1/32"]
    redis_tenants        = { app = { namespace = "x", databases = [7] } }
  }

  expect_failures = [var.redis_tenants]
}

run "redis_reserved_name_refused" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = ["192.0.2.1/32"]
    redis_tenants        = { default = { namespace = "x", databases = [1] } }
  }

  expect_failures = [var.redis_tenants]
}

run "redis_argocd_name_refused" {
  command = plan

  variables {
    postgres_allowed_ips = ["192.0.2.1/32"]
    redis_allowed_ips    = ["192.0.2.1/32"]
    redis_tenants        = { argocd = { namespace = "x", databases = [1] } }
  }

  expect_failures = [var.redis_tenants]
}

run "harbor_on_the_shared_databases" {
  command = plan

  variables {
    postgres_databases = { registry = { namespace = "harbor" } }
    redis_tenants      = { harbor = { namespace = "harbor", databases = [2, 3, 4, 5] } }
  }

  assert {
    condition = (
      helm_release.harbor.version == "1.19.4" &&
      helm_release.harbor.repository == "https://helm.goharbor.io" &&
      helm_release.harbor.namespace == "harbor" &&
      helm_release.harbor_access.namespace == "harbor" &&
      kubernetes_labels.harbor_namespace.metadata[0].name == "harbor" &&
      kubernetes_labels.harbor_namespace.labels["istio-injection"] == "enabled" &&
      yamldecode(helm_release.harbor.values[0]).database.external.host == local.postgres_pool_host &&
      yamldecode(helm_release.harbor.values[0]).database.external.existingSecret == "postgres-db-credentials" &&
      yamldecode(helm_release.harbor.values[0]).redis.external.addr == "${local.redis_host}:6379" &&
      yamldecode(helm_release.harbor.values[0]).persistence.persistentVolumeClaim.registry.storageClass == "local-path"
    )
    error_message = "Harbor is pinned, in the mesh, uses the shared PostgreSQL (tenant registry) and Valkey (tenant harbor), on local-path."
  }
}
