# StorageClass local-path: node-pinned volumes (each PV has nodeAffinity to the node holding its folder). minikube's
# "standard" class has none, so on 8 nodes a rescheduled pod can come up on an empty volume. Stays non-default;
# PostgreSQL (postgres.tf) uses it. Chart vendored from rancher/local-path-provisioner v0.0.37 (no Helm repository
# upstream). Values: deploy/minikube/storage/local-path.yaml.
resource "helm_release" "local_path_provisioner" {
  name             = "local-path-provisioner"
  namespace        = "local-path-storage"
  create_namespace = true
  chart            = "${local.minikube}/storage/local-path-provisioner"
  values           = [file("${local.minikube}/storage/local-path.yaml")]

  wait    = true
  timeout = var.helm_timeout
}
