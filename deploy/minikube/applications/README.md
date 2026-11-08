# Argo CD catalog for mk4

`kustomization.yaml` lists only the AppProject `mk-platform`, so `./deploy-argocd.sh` (`all`) has nothing to sync on
mk4 by default. The cluster add-ons that used to be Applications here (metrics-server, cert-manager, Grafana,
Prometheus, Loki + Alloy, OpenTelemetry Collector, Jaeger), and Istio and Kiali, are Terraform Helm releases:
[`minikube-infrastructure/`](../../../minikube-infrastructure/README.md). `workload-inventory.json` is empty for the
same reason. `mk-platform` no longer allows the namespaces Terraform owns (`kube-system`, `cert-manager`,
`istio-system`, `monitoring`, `observability`).

`deploy.py` does not update an AppProject that already exists:
`kubectl --context mk4 apply -f deploy/minikube/applications/project-mk-platform.yaml`.

RoboShop runs on EKS: its Applications are in `roboshop-ai/argocd-eks`. `scripts/argocd/deploy.py`
and `validate.py` read both catalogs together (`scripts/lib/repos.py`: `CATALOGS`); a name defined in both catalogs
is an error.

```bash
python3 scripts/argocd/validate.py --external-charts                     # also run by roboshop-ai's CI daily
```

None of these Applications sync automatically; never sync them with prune.

## Opt-in: `optional/`

`optional/` holds Applications that `deploy.py` offers only by name, never for `all`, and whose workloads are not in
`workload-inventory.json`. `validate.py` checks them with the catalog above.

| Application | Project | Source | Namespace |
|---|---|---|---|
| `harbor` | `mk-registry` (`optional/project-registry.yaml`) | harbor chart 1.19.4 + `deploy/minikube/harbor/values.yaml` + `deploy/minikube/harbor/manifests` | `harbor` |

```bash
python3 scripts/argocd/deploy.py harbor --context mk4   # prerequisites first: deploy/minikube/harbor/README.md
```
