locals {
  # deploy/minikube in this repository: values files and local charts.
  minikube = "${path.module}/../deploy/minikube"
}

variable "kubeconfig_path" {
  description = "Kubeconfig that contains the mk4 context."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Kubeconfig context of the Minikube cluster."
  type        = string
  default     = "mk4"
}

variable "helm_timeout" {
  description = "Seconds Helm waits for each release to become ready."
  type        = number
  default     = 600
}

variable "jithendar_com_dir" {
  description = "Checkout of the jithendar.com repository; its grafana/ folder holds the Jithendar.com dashboards."
  type        = string
  default     = "../../jithendar.com"
}

variable "adopt_existing_traefik" {
  description = "Import the Helm release traefik/traefik that bootstrap-cluster.sh installed before Terraform managed Traefik. false on a fresh cluster."
  type        = bool
  default     = false
}

variable "traefik_enabled" {
  description = "Keep Traefik installed (traefik.tf). true only while the host's HAProxy still forwards to Traefik; false uninstalls it and makes the Istio gateway require the PROXY protocol."
  type        = bool
  default     = false
}
