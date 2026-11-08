# Test fixture: the mk4 Argo CD catalog before the add-ons moved to Terraform

`tests/test_argocd_deploy.py` runs `scripts/argocd/deploy.py` against this copy of
`deploy/minikube/applications` as it was on 2026-11-08, before metrics-server, cert-manager and the observability stack
became Helm releases in `minikube-infrastructure/`. It only exercises deploy.py's mechanics (alignment, sync options,
waiting); nothing deploys these Applications any more.
