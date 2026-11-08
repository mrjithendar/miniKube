# Vault on Minikube

Deploy using Helm, like Traefik. No Terraform is needed.

```bash
bash deploy/minikube/deploy-vault.sh --dry-run
bash deploy/minikube/deploy-vault.sh
```

Overrides: `KUBE_CONTEXT=mk4 STORAGE_CLASS=standard`. Requires Helm, kubectl, Traefik with its Middleware CRD, and the existing ready `jithendar/jithendar-com` certificate and default TLSStore. Bootstrap the cluster and shared TLS first if missing.

The script installs/upgrades official chart 0.34.1 (Vault 2.0.4), creates namespace `vault`, and applies the single-resource manifests through `kustomization.yaml`. It does not initialize, unseal, restart, or delete stored data. Helm does not wait for readiness because new Vault servers are sealed.

| Path | Behavior |
| --- | --- |
| https://jithendar.com/vault | Redirect to /ui/ |
| /ui and descendants | Vault UI |
| /v1 and descendants | Vault API |

No new DNS or certificate is needed. These prefixes must be reserved for Vault; WordPress `/` and RoboShop `/roboshop` stay unchanged. The browser address changes to `/ui/`; this is not a custom `/vault/ui/` build. Sharing a browser origin with other applications offers less isolation than a dedicated hostname.

## Initialize once

```bash
kubectl --context=mk4 -n vault get pods,pvc,ingress
kubectl --context=mk4 -n vault exec vault-0 -- vault status
```

An unready, sealed/uninitialized server is expected. Open the UI, initialize with 5 shares and a threshold of 3, and save the shares and initial root token securely outside the cluster/repository. Submit three different shares to unseal. Establish restricted administrator authentication and audit logging before revoking the initial root token. There is no default username/password.

After restarts, unseal again using the original shares; never reinitialize existing storage. The script never generates keys or copies existing RoboShop Secrets into Vault. See the official [initialization guide](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/helm/run).

## Lab boundaries and troubleshooting

One persistent server, 10Gi PVC, 256Mi memory request/1Gi limit; injector and dev mode disabled. TLS terminates at Traefik; internal traffic to Vault uses HTTP. This is not HA or a production secret infrastructure design. Back up storage and unseal keys before changing Minikube. Never delete PVCs to fix a sealed pod.

- Missing TLS: restore the shared jithendar.com certificate/TLSStore.
- UI 503: inspect pod logs, scheduling, PVC binding and `vault-ui` EndpointSlices. The UI service publishes sealed pods for initialization.
- Helm error: inspect `helm --kube-context mk4 -n vault status vault`; avoid uninstalling a persistent installation as a retry strategy.
- If you applied the previous Terraform configuration yourself, do not keep applying it alongside this script. Back up its state and relinquish Terraform ownership without destroying live resources before switching managers.

## Offline validation

```bash
bash -n deploy/minikube/deploy-vault.sh
bash deploy/minikube/deploy-vault.sh --dry-run
helm template vault vault --repo https://helm.releases.hashicorp.com --version 0.34.1 --namespace vault -f deploy/minikube/vault/values.yaml > /tmp/roboshop-vault-rendered.yaml
python3 tests/test_vault_chart.py /tmp/roboshop-vault-rendered.yaml
```
