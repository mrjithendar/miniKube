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
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}
