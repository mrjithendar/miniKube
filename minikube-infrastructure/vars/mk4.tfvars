# terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
kube_context = "mk4"

# Who may reach /postgres and postgres.jithendar.com (same addresses as jithendar.com's /db allowlist,
# jithendar.com minikube/db-access-10-allowlist.yaml): the owner's Mac (home IP on 2026-11-04, can change) and the
# Hetzner host itself.
postgres_allowed_ips = ["122.177.247.138/32", "46.224.38.14/32"]

# Applications on the shared cluster (postgres.tf). Each gets database + owner role <key> and Secret
# <namespace>/postgres-db-credentials. Listed ahead of moving them off their own PostgreSQL pods (README.md, PostgreSQL,
# "Moving an application"); until an application switches, its database here stays empty.
postgres_databases = {
  jithendar = { namespace = "jithendar" }   # jithendar.com site; today jithendar/jithendar-postgres-0 (PG 18)
  moks      = { namespace = "moks" }        # MOKS portal; today moks/moks-postgres-0 (PG 17: dump and restore)
  registry  = { namespace = "harbor" }      # Harbor core; today harbor/harbor-postgres-0 (PG 18), database "registry"
  shipping  = { namespace = "roboshop-ai" } # RoboShop shipping; today roboshop-ai/shipping-db (PG 18)
}
