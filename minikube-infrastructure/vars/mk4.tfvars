# terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
kube_context = "mk4"

# Who may reach /postgres and postgres.jithendar.com (same addresses as jithendar.com's /db allowlist,
# jithendar.com minikube/db-access-10-allowlist.yaml): the owner's Mac (home IP, still current on 2026-11-10). The
# Minikube host (46.224.38.14) is gone; that address now belongs to a k3s worker, so it is no longer listed.
postgres_allowed_ips = ["122.177.247.138/32"]

# Who may reach /redis and redis.jithendar.com (redis.tf): the same address.
redis_allowed_ips = ["122.177.247.138/32"]

# Applications on the shared cluster (postgres.tf). Each gets database + owner role <key> and Secret
# <namespace>/postgres-db-credentials. Listed ahead of moving them off their own PostgreSQL pods (README.md, PostgreSQL,
# "Moving an application"); until an application switches, its database here stays empty.
postgres_databases = {
  jithendar = { namespace = "jithendar" }   # jithendar.com site; today jithendar/jithendar-postgres-0 (PG 18)
  moks      = { namespace = "moks" }        # MOKS portal; today moks/moks-postgres-0 (PG 17: dump and restore)
  registry  = { namespace = "harbor" }      # Harbor core; today harbor/harbor-postgres-0 (PG 18), database "registry"
  shipping  = { namespace = "roboshop-ai" } # RoboShop shipping; today roboshop-ai/shipping-db (PG 18)
}

# Applications on the shared Valkey (redis.tf). Each gets ACL user <key>, limited to its database numbers, and Secret
# <namespace>/redis-<key>-credentials, which the application reads (no application runs its own Redis any more).
# Argo CD is a built-in tenant (argocd.tf: user argocd, database 7); "argocd" and database 7 are reserved here.
redis_tenants = {
  practice = { namespace = "jithendar", databases = [1] }       # practice executor queue (EXECUTOR_REDIS_URL)
  harbor   = { namespace = "harbor", databases = [2, 3, 4, 5] } # core, jobservice, registry, trivy
  roboshop = { namespace = "roboshop-ai", databases = [6] }     # cart and shipping cache
}
