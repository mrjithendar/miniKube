> Deployment-script cleanup: imperative application deployment commands in this
> document are retired. Use the Argo CD catalog and the five supported commands
> documented in `docs/operations/scripts.md`. Historical examples below are not
> runnable deployment instructions.

# Langfuse Cloud

Self-hosted Langfuse is retired. No custom image build, ClickHouse, PostgreSQL,
Valkey or object storage is needed for Cloud tracing.

## Remove the old installation

```bash
bash deploy/minikube/remove-langfuse.sh
```

This removes Langfuse workloads, Services, Ingresses and Certificates in namespace
`langfuse`. It preserves PVCs, Secrets, ConfigMaps and Helm release metadata for
recovery. Do not upgrade the retired Helm release. Historical local traces are
not migrated to Cloud. The old subdomain DNS record may be removed separately.

## Connect Cloud without entering keys interactively

Create a project in your preferred Langfuse Cloud region. Self-hosted project
keys do not authenticate to Cloud. Update `roboshop-ai/langfuse-credentials` with
its **Cloud** `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL`.
Then run:

```bash
bash deploy/minikube/connect-langfuse.sh
```

The script copies only Langfuse settings to `roboshop-secrets` and restarts
moks without logging keys. If the source Secret is absent, it reuses
keys in `roboshop-secrets`. Rerun after rotating keys.

Use the URL matching your Cloud project:

| Region | LANGFUSE_BASE_URL |
|---|---|
| EU | https://cloud.langfuse.com |
| US | https://us.cloud.langfuse.com |
| Japan | https://jp.cloud.langfuse.com |
| HIPAA US | https://hipaa.cloud.langfuse.com |

You may provide the URL without changing the source Secret:

```bash
LANGFUSE_BASE_URL=https://cloud.langfuse.com bash deploy/minikube/connect-langfuse.sh
```

[Official regions](https://langfuse.com/security/data-regions). Cloud receives
moks telemetry outside your cluster; account plan limits and charges
apply. Existing asynchronous metadata export is retained. Optional tracing
failures do not block shopping. No data migration or Cloud account provisioning
is performed by these scripts.
