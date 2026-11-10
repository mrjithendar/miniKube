locals {
  # deploy/minikube in this repository: values files and local charts.
  minikube = "${path.module}/../deploy/minikube"

  # One extra value per local chart: a hash of every file in the chart directory. The helm provider plans only chart
  # version and values, so without it a template edit under an unchanged Chart.yaml version plans "No changes"
  # (the /prometheus and /loki VirtualServices never reached mk4, 2026-11-09). The charts ignore the value.
  local_charts = [
    "cert-manager/issuers", "harbor/access", "istio/edge", "istio/mesh", "observability/grafana/dashboards", "observability/jaeger",
    "observability/loki", "observability/otel-collector", "observability/prometheus", "postgres/access",
    "redis/access", "storage/local-path-provisioner",
  ]
  chart_checksum = { for dir in local.local_charts : dir => yamlencode({
    chartChecksum = sha1(join("\n", [
      for f in sort(fileset("${local.minikube}/${dir}", "**")) : "${f} ${filesha1("${local.minikube}/${dir}/${f}")}"
    ]))
  }) }
}

variable "kubeconfig_path" {
  description = "Kubeconfig that contains the mk4 context."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Kubeconfig context of the Minikube cluster."
  type        = string
  default     = "mk4"
}

variable "helm_timeout" {
  description = "Seconds Helm waits for each release to become ready."
  type        = number
  default     = 600
}

variable "jithendar_com_dir" {
  description = "Checkout of the jithendar.com repository; its grafana/ folder holds the Jithendar.com dashboards."
  type        = string
  default     = "../../jithendar.com"
}



variable "postgres_allowed_ips" {
  description = "Client addresses (CIDRs) allowed through https://jithendar.com/postgres/ and postgres.jithendar.com:443 (postgres.tf). Everything else is refused on the gateway."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for ip in var.postgres_allowed_ips : can(cidrhost(ip, 0))])
    error_message = "Each entry must be a CIDR, e.g. 203.0.113.7/32."
  }
}

variable "postgres_ui_admin_email" {
  description = "pgAdmin's login at https://jithendar.com/postgres/ (postgres.tf); its password is generated, Secret postgres-db/postgres-ui-admin."
  type        = string
  default     = "admin@jithendar.com"
}

variable "postgres_databases" {
  description = "Applications on the shared PostgreSQL cluster (postgres.tf): database and owning login role named by the key, connection Secret postgres-db-credentials written into namespace. Keys are PostgreSQL identifiers."
  type = map(object({
    namespace        = string
    connection_limit = optional(number, 50)
  }))
  default = {}
  validation {
    condition     = alltrue([for name in keys(var.postgres_databases) : can(regex("^[a-z][a-z0-9_]{0,47}$", name))])
    error_message = "Database names: lower case letters, digits and _, starting with a letter, at most 48 characters."
  }
  validation {
    # Names CloudNativePG itself uses: the bootstrap owner, the superuser, replication and PgBouncer roles.
    condition     = length(setintersection(keys(var.postgres_databases), ["app", "postgres", "streaming_replica", "cnpg_pooler_pgbouncer", "template0", "template1"])) == 0
    error_message = "app, postgres, streaming_replica, cnpg_pooler_pgbouncer, template0 and template1 are reserved."
  }
}

variable "redis_tenants" {
  description = "Applications on the shared Valkey (redis.tf): ACL user named by the key, limited to its database numbers, connection Secret redis-<key>-credentials written into namespace."
  type = map(object({
    namespace = string
    # Database numbers (0-31) this application may SELECT; the first is the uri's default. No two tenants share one.
    databases = list(number)
  }))
  default = {}
  validation {
    condition     = alltrue([for name in keys(var.redis_tenants) : can(regex("^[a-z][a-z0-9-]{0,40}$", name)) && !contains(["default", "console", "argocd"], name)])
    error_message = "Tenant names: lower case letters, digits and -, starting with a letter, at most 41 characters; \"default\" (admin), \"console\" (RedisInsight) and \"argocd\" (argocd.tf) are reserved."
  }
  validation {
    condition     = alltrue([for t in values(var.redis_tenants) : length(t.databases) > 0 && alltrue([for d in t.databases : d >= 0 && d <= 31 && floor(d) == d])])
    error_message = "Each tenant needs at least one database number, each an integer from 0 to 31 (databases 32 in deploy/minikube/redis/values.yaml)."
  }
  validation {
    condition     = length(flatten([for t in values(var.redis_tenants) : t.databases])) == length(distinct(flatten([for t in values(var.redis_tenants) : t.databases])))
    error_message = "A database number belongs to one tenant only."
  }
  validation {
    condition     = length(setintersection(flatten([for t in values(var.redis_tenants) : t.databases]), local.argocd_redis.databases)) == 0
    error_message = "Database 7 is Argo CD's (argocd.tf, local.argocd_redis)."
  }
}

variable "redis_allowed_ips" {
  description = "Client addresses (CIDRs) allowed through https://jithendar.com/redis/ and redis.jithendar.com:443 (redis.tf). Everything else is refused on the gateway."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for ip in var.redis_allowed_ips : can(cidrhost(ip, 0))])
    error_message = "Each entry must be a CIDR, e.g. 203.0.113.7/32."
  }
}

variable "dockerhub_username" {
  description = "Docker Hub user for Harbor's private arm64 images (harbor.tf, Secret harbor/dockerhub-pull). CI: repository secret DOCKER_USERNAME; locally: export TF_VAR_dockerhub_username."
  type        = string
  default     = ""
}

variable "dockerhub_password" {
  description = "Docker Hub access token (read-only is enough) for that user. CI: repository secret DOCKER_PASSWORD; locally: export TF_VAR_dockerhub_password. Never in a tfvars file."
  type        = string
  default     = ""
  sensitive   = true
}
