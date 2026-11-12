#!/usr/bin/env bash
# One-time: gives .github/workflows/create-mk-infrastructure.yml access to mk4 and to its Terraform state.
#   1. applies minikube-infrastructure/ci/github-terraform-serviceaccount.yaml (cluster-admin ServiceAccount + token);
#   2. creates the GitHub environment "mk4", deployable from master only;
#   3. stores what the workflow needs:
#        MK4_TERRAFORM_TOKEN      environment secret (mk4)   ServiceAccount token
#        MK4_API_SERVER           repository variable        cluster API URL from the kubeconfig context
#        MK4_CA_DATA              repository variable        base64 cluster CA
#        MK4_TERRAFORM_ROLE_ARN   repository variable        AWS role github-tools-minikube-terraform (tools repository: infrastructure/)
# It does NOT set MINIKUBE_TERRAFORM_APPLY: set that to true yourself once minikube-infrastructure/README.md
# ("Moving the live mk4 add-ons") is done, or the first CI apply fails on the old installations.
#
#   scripts/setup-minikube-terraform-ci.sh            # everything above
#   SKIP_GH=1 scripts/setup-minikube-terraform-ci.sh  # only apply and test; set the GitHub values yourself
#   ROTATE=1 scripts/setup-minikube-terraform-ci.sh   # replace the token (the old one stops working)
#
# The token goes straight into `gh secret set` (stdin) and is never printed.
# Needs: kubectl with cluster-admin on context mk4; AWS CLI (to read the role ARN); gh signed in with admin on the
# repository (unless SKIP_GH=1). The AWS role exists once tools/infrastructure/github-minikube-terraform.tf is applied.
set -euo pipefail

REPO="${GITHUB_REPO:-mrjithendar/miniKube}"
CONTEXT="${KUBE_CONTEXT:-mk4}"
NS="kube-system"
SA="github-minikube-terraform"
ROLE="github-tools-minikube-terraform"
MANIFEST="minikube-infrastructure/ci/github-terraform-serviceaccount.yaml"
TEMPLATE=".github/mk4-terraform-kubeconfig.yaml"
k() { kubectl --context "$CONTEXT" "$@"; }

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."

MK4_API_SERVER="$(kubectl config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.server}')"
MK4_CA_DATA="$(kubectl config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
[[ -n "$MK4_CA_DATA" ]] || MK4_CA_DATA="$(base64 < "$(kubectl config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.certificate-authority}')" | tr -d '\n')"
[[ -n "$MK4_API_SERVER" && -n "$MK4_CA_DATA" ]] || { echo "Could not read server/CA of context $CONTEXT." >&2; exit 1; }
case "$MK4_API_SERVER" in
  *127.0.0.1*|*localhost*|*192.168.*) echo "Context $CONTEXT points at $MK4_API_SERVER, which GitHub's runners cannot reach. Use the public k3s API of the Hetzner master (https://<master IP>:6443)." >&2; exit 1 ;;
esac

echo "→ Cluster $MK4_API_SERVER, ServiceAccount $NS/$SA, repository $REPO"
if [[ "${ROTATE:-0}" == "1" ]]; then
  echo "→ Deleting the old token"
  k -n "$NS" delete secret "$SA-token" --ignore-not-found
fi

echo "→ Applying $MANIFEST"
k apply -f "$MANIFEST"

echo "→ Waiting for the token"
MK4_TERRAFORM_TOKEN=""
for _ in $(seq 1 20); do
  MK4_TERRAFORM_TOKEN="$(k -n "$NS" get secret "$SA-token" -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
  [[ -n "$MK4_TERRAFORM_TOKEN" ]] && break
  sleep 1
done
[[ -n "$MK4_TERRAFORM_TOKEN" ]] || { echo "Token was not issued for $NS/$SA-token." >&2; exit 1; }
export MK4_API_SERVER MK4_CA_DATA MK4_TERRAFORM_TOKEN

# Render the template exactly as the workflow does and check it can do what the releases need.
echo "→ Testing $TEMPLATE with these values"
KCFG="$(mktemp)"
trap 'rm -f "$KCFG"' EXIT
perl -pe 's/\$\{(MK4_[A-Z_]+)\}/$ENV{$1}/g' "$TEMPLATE" > "$KCFG"
for verb in "create customresourcedefinitions.apiextensions.k8s.io" "escalate clusterroles.rbac.authorization.k8s.io" \
            "patch deployments -n kube-system" "create secrets -n istio-system" "patch ingresses.networking.k8s.io -n monitoring"; do
  # shellcheck disable=SC2086
  [[ "$(kubectl --kubeconfig "$KCFG" auth can-i $verb)" == yes ]] || { echo "The rendered kubeconfig cannot '$verb'." >&2; exit 1; }
done

MK4_TERRAFORM_ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text 2>/dev/null || true)"
[[ "$MK4_TERRAFORM_ROLE_ARN" == arn:aws:iam::* ]] \
  || { echo "AWS role $ROLE not found. Apply infrastructure/github-minikube-terraform.tf in the tools repository first (merge it to tools master)." >&2; exit 1; }

if [[ "${SKIP_GH:-0}" == "1" ]]; then
  cat <<OUT
Done. In $REPO → Settings:
  Environments → New environment "mk4" → Deployment branches: selected branches, add "master"
  Environment mk4 secret  MK4_TERRAFORM_TOKEN    = copy with: kubectl --context $CONTEXT -n $NS get secret $SA-token -o jsonpath='{.data.token}' | base64 -d | pbcopy
  Repository variables    MK4_API_SERVER         = $MK4_API_SERVER
                          MK4_CA_DATA            = (kubectl config view --raw --minify --context $CONTEXT)
                          MK4_TERRAFORM_ROLE_ARN = $MK4_TERRAFORM_ROLE_ARN
  Then, after minikube-infrastructure/README.md steps 1–5: variable MINIKUBE_TERRAFORM_APPLY = true
OUT
  exit 0
fi

echo "→ Environment mk4 (master only)"
gh api -X PUT "repos/$REPO/environments/mk4" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
if ! gh api "repos/$REPO/environments/mk4/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx master; then
  gh api -X POST "repos/$REPO/environments/mk4/deployment-branch-policies" -f name=master -f type=branch >/dev/null
fi

echo "→ Storing secret MK4_TERRAFORM_TOKEN (environment mk4) and the variables"
printf '%s' "$MK4_TERRAFORM_TOKEN" | gh secret set MK4_TERRAFORM_TOKEN --env mk4 --repo "$REPO"
unset MK4_TERRAFORM_TOKEN
gh variable set MK4_API_SERVER --repo "$REPO" --body "$MK4_API_SERVER"
gh variable set MK4_CA_DATA --repo "$REPO" --body "$MK4_CA_DATA"
gh variable set MK4_TERRAFORM_ROLE_ARN --repo "$REPO" --body "$MK4_TERRAFORM_ROLE_ARN"
echo "Done. After minikube-infrastructure/README.md steps 1–5 (the Argo CD apps are gone), turn the apply job on:"
echo "  gh variable set MINIKUBE_TERRAFORM_APPLY --repo $REPO --body true"
