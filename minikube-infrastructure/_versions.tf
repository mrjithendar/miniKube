# Cluster add-ons on the mk4 Minikube cluster, installed as Helm releases by Terraform (README.md).
# Run by a person from a machine whose kubeconfig reaches mk4. Separate from the tools repository's infrastructure/ (AWS): own state.
terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    # Only for the Kiali signing-key Secret, which no chart renders without regenerating it.
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  backend "s3" {
    bucket       = "jithendardharmapuri"
    key          = "terraform/minikube-mk4.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "helm" {
  # Plan the rendered manifests, not only chart versions and values. Without this a template change in a local chart
  # (deploy/minikube/...) whose Chart.yaml version stayed the same planned "No changes": the delegate
  # VirtualServices for /prometheus and /loki never reached mk4 (found 2026-11-09).
  experiments = {
    manifest = true
  }
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}
