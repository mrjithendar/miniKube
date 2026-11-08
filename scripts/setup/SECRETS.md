# Minikube credentials

Run from the repository root (Python 3 and kubectl required; RoboShop/ECR helpers also require the existing PyYAML dependency):

```bash
./create-secrets.sh --context mk4
./create-secrets.sh --context mk4 --apply
```

The first command is an offline preview. The second creates missing namespaces and internal credentials for:

| Namespace | Secret | Purpose |
| --- | --- | --- |
| roboshop-ai | roboshop-secrets | Database passwords, service tokens, encryption key, superadmin |
| monitoring | monitoring-auth | Shared Grafana/Prometheus admin credentials |
| jithendar | wordpress-credentials | WordPress admin, MySQL and Redis passwords |
| jfrog | jfrog-bootstrap | Optional JFrog database, admin and encryption credentials |
| harbor | harbor-credentials | Optional Harbor admin, encryption key, component secrets, PostgreSQL and Redis passwords ([details](../../deploy/minikube/harbor/README.md)); needs `htpasswd` |

Include JFrog and Harbor with `--all`, or select installations with `--services monitoring wordpress`. Add `--ecr` to refresh repository-managed ECR pull secrets using your existing AWS authentication. ECR tokens expire after 12 hours; this command does not install the scheduled refresher.

```bash
./create-secrets.sh --context mk4 --all --ecr --apply
```

Existing secrets are reused, never rotated or deleted. Partial secrets require repair from original credentials. Generated values travel through stdin and are not printed. No pods are restarted, passwords in existing databases are not reset, and no deployments or controllers are installed.

If a selected namespace has PVCs but its installation secret is missing, creation stops before writes. This includes Terminating PVCs. Restore the original secret from your secure backup; random replacement passwords cannot recover database access or encrypted data. Namespace-level PVC checks are deliberately conservative. Retained PVs or external databases without PVCs cannot be detected by this check: restore their original credentials too.

This is not a generator for arbitrary Kubernetes secrets. cert-manager owns TLS keys, Argo CD owns its initial admin credentials, and Helm owns release records. These remain untouched. Turnstile, Razorpay, SES, AWS, Langfuse and OpenAI credentials must come from their providers.

Verify locally without Kubernetes or AWS calls:

```bash
python3 -m unittest discover -s tests -p test_create_secrets.py
bash -n create-secrets.sh
./create-secrets.sh --all --ecr --dry-run
```
