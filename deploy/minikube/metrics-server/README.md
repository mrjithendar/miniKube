> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. This component is a Terraform Helm release:
> `minikube-infrastructure/README.md`. Historical examples below are not
> runnable deployment instructions.

# Metrics Server

```bash
bash deploy/minikube/deploy-metrics-server.sh
```

Installs the official Kubernetes Metrics Server Helm chart **3.14.0** (server **v0.9.0**) in `kube-system`. Follows the selected Minikube profile; set `KUBE_CONTEXT` and `KUBECONFIG` when connecting remotely. It waits for the Deployment, aggregated metrics API and initial samples.

```bash
kubectl top nodes
kubectl top pods -A
kubectl get apiservice v1beta1.metrics.k8s.io
```

Metrics Server provides recent CPU/memory measurements for `kubectl top` and resource-based horizontal pod autoscaling. It does not replace Prometheus's historical metrics and does not create HPAs or a public web page.

The values enable `--kubelet-insecure-tls` for this Minikube sandbox's kubelet certificates. Remove that flag when using kubelets with correctly signed certificates and node address SANs. This setting does not disable TLS verification in your local kubeconfig. The upstream chart defaults are used for its internal aggregated API serving certificate.

Do not also enable Minikube's Metrics Server addon; this Helm release owns the installation. If another installation exists, reconcile ownership instead of deploying a second server.

Troubleshooting:

```bash
kubectl -n kube-system get pods -l app.kubernetes.io/name=metrics-server
kubectl -n kube-system logs deployment/metrics-server --tail=100
kubectl describe apiservice v1beta1.metrics.k8s.io
```

Allow an initial sampling interval before expecting `kubectl top` output. Pending pods indicate scheduling/capacity issues; scrape failures appear in server logs. Resource requests are 100m CPU/128 MiB, with limits of 300m/256 MiB.

Reference: [official Metrics Server](https://github.com/kubernetes-sigs/metrics-server).
