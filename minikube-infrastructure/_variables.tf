locals {
  # deploy/minikube in this repository: values files and local charts.
  minikube = "${path.module}/../deploy/minikube"
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
