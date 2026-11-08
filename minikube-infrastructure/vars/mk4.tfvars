# terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars
kube_context = "mk4"
# mk4 already runs the Helm release traefik/traefik (installed by bootstrap-cluster.sh on 2026-10-27): adopt it.
adopt_existing_traefik = true
# Traefik until the host forwards to the Istio gateway (deploy/minikube/istio/README.md, "Leaving Traefik"); then false.
traefik_enabled = true
