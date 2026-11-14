# OPA Gatekeeper

Admission policies for mk4, plus a regular audit of what is already running. All three parts are Terraform Helm
releases in `minikube-infrastructure/gatekeeper.tf`, namespace `gatekeeper-system`:

| Release | Chart | What |
|---|---|---|
| `gatekeeper` | open-policy-agent `gatekeeper` 3.23.1 | Webhook (2 replicas) and audit. Values: `values.yaml` |
| `gatekeeper-library` | local `library/` | ConstraintTemplates, vendored unchanged from gatekeeper-library |
| `gatekeeper-constraints` | local `constraints/` | The constraints: what is enforced, and where. Values: `constraints/values.yaml` |

Terraform waits 30 s between the library and the constraints (`time_sleep.gatekeeper_constraint_kinds`). Gatekeeper
creates each template's constraint kind a few seconds after the template, and Helm cannot wait for that.

## Policies

Every constraint matches Pods, in every namespace except `kube-system` and `gatekeeper-system`.

| Constraint | Kind | Action | Also excluded |
|---|---|---|---|
| `disallow-privileged-containers` | `K8sPSPPrivilegedContainer` | deny | |
| `disallow-host-namespaces` | `K8sPSPHostNamespace` (hostPID, hostIPC) | deny | |
| `disallow-host-network-and-ports` | `K8sPSPHostNetworkingPorts` (hostNetwork, any hostPort) | deny | |
| `disallow-hostpath-volumes` | `K8sPSPHostFilesystem` | deny | `istio-system` (istio-cni), `system-upgrade` (k3s upgrades), `local-path-storage` (volume helper Pods) |
| `disallow-latest-image-tag` | `K8sDisallowedTags` (`:latest` or no tag) | warn | |
| `require-memory-limits` | `K8sRequiredResources` (memory limit on every container) | warn | |

The actions come from a scan of every running Pod on 2026-11-14. The four `deny` policies had no violations outside
the excluded namespaces. The other two had violations, so they only warn for now:

- `:latest`: all application images in `jithendar`, `moks` and `roboshop-ai`.
- No memory limit: containers in `argocd`, `cnpg-system`, `istio-system` and `system-upgrade`.

`warn` admits the Pod. `kubectl` prints the warning, and audit lists the Pod in the constraint's status. Once the
violations are fixed, switch the action to `deny` in `constraints/values.yaml`. `dryrun` reports through audit only.

The constraints match Pods, not the workloads that create them. So a Deployment that breaks a `deny` policy is
accepted, and its ReplicaSet then fails to create Pods: `kubectl -n <ns> describe rs` shows
`admission webhook "validation.gatekeeper.sh" denied the request`.

There are two enforcement paths:

- **The four pod-security templates** have CEL code. For each of their constraints, Gatekeeper generates a
  ValidatingAdmissionPolicy and binding (`gatekeeper-<template>`, `kubectl get validatingadmissionpolicy`). The API
  server enforces these itself, so they keep refusing Pods even while Gatekeeper's pods are down. The constraint's
  `excludedNamespaces` apply there too.
- **The other two templates** (tags, memory limits) are Rego only and go through Gatekeeper's webhook. The webhook
  fails open (`failurePolicy: Ignore`, 3 s timeout): if Gatekeeper is down, requests are admitted, and the next audit
  reports them.

## Checking

```bash
kubectl --context mk4 get constraints                     # ENFORCEMENT-ACTION and TOTAL-VIOLATIONS per constraint
kubectl --context mk4 get constrainttemplates             # each must show its kind created
kubectl --context mk4 describe k8sdisallowedtags disallow-latest-image-tag   # the violating Pods (up to 50)
kubectl --context mk4 -n gatekeeper-system logs deploy/gatekeeper-controller-manager | grep denied
```

## Changing policies

- **Action or exclusions:** edit `constraints/values.yaml`. Every constraint takes `enforcementAction` and
  `excludedNamespaces`, added to the shared `excludedNamespaces`.
- **A new policy from the library:** copy upstream's `library/<group>/<policy>/template.yaml` into `library/library/`,
  add a one-line `library/templates/<template name>.yaml` (`{{ .Files.Get "library/<file>.yaml" }}`), then add a
  constraint template and values entry in `constraints/`. Start with `dryrun` or `warn` and read the audit results first.

## Updating the library

The templates are upstream's files, unchanged, from
[open-policy-agent/gatekeeper-library](https://github.com/open-policy-agent/gatekeeper-library) at commit
`5982d6715e3046343bbf5632d35f7e71b92b181a` (2026-11-11). The library has no release tags. To update, fetch the same
paths at a newer commit, diff them, and bump `library/Chart.yaml`'s version:

| File | Upstream path (`library/...`) |
|---|---|
| `privileged-containers.yaml` | `pod-security-policy/privileged-containers/template.yaml` |
| `host-namespaces.yaml` | `pod-security-policy/host-namespaces/template.yaml` |
| `host-network-ports.yaml` | `pod-security-policy/host-network-ports/template.yaml` |
| `host-filesystem.yaml` | `pod-security-policy/host-filesystem/template.yaml` |
| `disallowedtags.yaml` | `general/disallowedtags/template.yaml` |
| `containerresources.yaml` | `general/containerresources/template.yaml` |
