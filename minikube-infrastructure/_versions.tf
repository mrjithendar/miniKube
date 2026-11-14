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
    # Only for the pause between the Gatekeeper templates and their constraints (gatekeeper.tf).
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }

  backend "s3" {
    bucket = "jithendardharmapuri"
    # mk4 is the k3s cluster on Hetzner Cloud since 2026-11-10 (hetzner-k3s, ../../hetzner-k3s.yaml). The Minikube
    # mk4's state stays at terraform/minikube-mk4.tfstate for reference; that server was deleted.
    key          = "terraform/k3s-mk4.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "helm" {
  # No experiments.manifest: on 2026-11-09 it failed applies with "Provider produced inconsistent result" (objects in
  # namespaces created by the release, labels Helm adds on adoption, and once another release's manifest). Template
  # changes in local charts are caught by local.chart_checksum (_variables.tf) instead.
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}
