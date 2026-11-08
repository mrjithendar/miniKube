"""Install cluster controllers in order; never implicitly deploy business apps."""
import argparse
import base64
import os
import json
from pathlib import Path
import re
import shutil
import secrets
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = Path(os.environ.get('ROBOSHOP_AI_DIR', ROOT.parent / 'roboshop-ai')).resolve()
# cert-manager is not here: minikube-infrastructure/ (Terraform) installs it with the other add-ons, after this.
CONTROLLERS = (
    ('traefik', 'https://traefik.github.io/charts', '42.0.0', 'traefik'),
)


def refresh_pull_secrets(context):
    """ECR tokens stay in memory/stdin, never command arguments, files or logs."""
    region = os.getenv('AWS_REGION', 'us-east-1')
    registry = os.getenv('ECR_REGISTRY', '881026659929.dkr.ecr.us-east-1.amazonaws.com')
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', region):
        raise RuntimeError('Invalid AWS_REGION')
    if not re.fullmatch(r'\d{12}\.dkr\.ecr\.' + re.escape(region) + r'\.amazonaws\.com(?:\.cn)?', registry):
        raise RuntimeError('ECR_REGISTRY must be an ECR hostname in AWS_REGION')
    token = subprocess.run(['aws', 'ecr', 'get-login-password', '--region', region],
                           capture_output=True, text=True, timeout=45)
    if token.returncode or not token.stdout.strip():
        raise RuntimeError('Cannot obtain ECR credentials. Check AWS authentication and ecr:GetAuthorizationToken permission; no pull secrets changed.')
    auth = base64.b64encode(('AWS:' + token.stdout.strip()).encode()).decode()
    docker_config = json.dumps({'auths': {registry: {'auth': auth}}})
    kube = ['kubectl', '--context', context, '--request-timeout=20s']
    # Explicit repository-managed namespaces only; never modify kube-system or
    # unrelated namespaces. Kubernetes has no cluster-scoped imagePullSecret.
    for path in sorted((ROOT / 'deploy/minikube/namespaces').glob('*.yaml')):
        namespace = path.stem
        if namespace == 'kube-system':
            continue
        subprocess.check_call(kube + ['apply', '--server-side', '-f', str(path)])
        secret = {'apiVersion': 'v1', 'kind': 'Secret',
                  'metadata': {'name': 'ecr-pull', 'namespace': namespace},
                  'type': 'kubernetes.io/dockerconfigjson',
                  'data': {'.dockerconfigjson': base64.b64encode(docker_config.encode()).decode()}}
        # Only this named pull secret is refreshed. Do not touch app passwords.
        result = subprocess.run(kube + ['apply', '--server-side', '--force-conflicts',
            '--field-manager=roboshop-ecr', '-f', '-'], input=json.dumps(secret),
            capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'Failed to refresh ecr-pull in {namespace}; check Kubernetes permissions. Secret contents suppressed.')
        account = {'apiVersion': 'v1', 'kind': 'ServiceAccount',
                   'metadata': {'name': 'default', 'namespace': namespace},
                   'imagePullSecrets': [{'name': 'ecr-pull'}]}
        subprocess.run(kube + ['apply', '--server-side', '--field-manager=roboshop-ecr', '-f', '-'],
                       input=json.dumps(account), text=True, check=True)
        print(f'{namespace}: ecr-pull refreshed; default ServiceAccount configured.')
    print('ECR tokens expire after 12 hours. Refresh manually with --pull-secrets-only or enable --schedule-pull-secrets.')


INTERNAL_PASSWORD_KEYS = (
    'MONGO_ROOT_PASSWORD', 'MONGO_APP_PASSWORD', 'SHIPPING_DB_PASSWORD',
    'SHIPPING_APP_PASSWORD', 'REDIS_PASSWORD', 'RABBITMQ_PASSWORD',
    'USER_SERVICE_TOKEN', 'PAYMENT_SERVICE_TOKEN', 'LLM_SERVICE_TOKEN',
    'SUPERADMIN_PASSWORD',
)


# Volumes whose databases keep the credentials they were initialized with.
DATA_CLAIMS = ('mongodb-data', 'data-mongodb-0', 'shipping-postgres', 'rabbitmq-data')


def ensure_app_secrets(context, accept_new_credentials=False):
    """Create fresh credentials once; existing credentials are never rotated."""
    kube = ['kubectl', '--context', context, '--request-timeout=20s']
    subprocess.check_call(kube + ['apply', '--server-side', '-f', str(APP / 'argocd-eks/apps/roboshop/roboshop-ai-namespace.yaml')])
    existing = subprocess.check_output(kube + ['-n', 'roboshop-ai', 'get', 'secret',
        'roboshop-secrets', '--ignore-not-found', '-o', 'json'], text=True)
    if existing.strip():
        missing = set(INTERNAL_PASSWORD_KEYS + ('SUPERADMIN_EMAIL', 'AUTH_ENCRYPTION_KEY')) - json.loads(existing).get('data', {}).keys()
        if missing:
            raise RuntimeError('Existing roboshop-secrets preserved; missing keys: ' + ', '.join(sorted(missing)) + '. Restore the original values; bootstrap will not rotate existing credentials.')
        print('Reusing roboshop-secrets; passwords and encryption key unchanged.')
        return
    # New random passwords do not match databases that already hold data: MongoDB
    # and PostgreSQL only apply credentials when their volume is first initialized.
    claims = subprocess.check_output(kube + ['-n', 'roboshop-ai', 'get', 'pvc', '-o',
        'jsonpath={.items[*].metadata.name}'], text=True).split()
    existing_data = sorted(set(claims) & set(DATA_CLAIMS))
    if existing_data and not accept_new_credentials:
        raise RuntimeError('roboshop-secrets is missing but data volumes exist: ' + ', '.join(existing_data) + '. '
                           'Restore the original Secret from backup. Only if it is lost, rerun with --accept-new-credentials '
                           'and then reset the database users to the new values (MongoDB: scripts/reset-mongo-password.sh).')
    values = {key: secrets.token_hex(32) for key in INTERNAL_PASSWORD_KEYS}
    values['SUPERADMIN_EMAIL'] = 'team@jithendar.com'
    values['AUTH_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    document = {'apiVersion': 'v1', 'kind': 'Secret',
                'metadata': {'name': 'roboshop-secrets', 'namespace': 'roboshop-ai'},
                'type': 'Opaque', 'stringData': values}
    # Create, never apply/replace: a concurrent creator cannot be overwritten.
    result = subprocess.run(kube + ['create', '-f', '-'], input=json.dumps(document),
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Could not create roboshop-secrets; check permissions or rerun if another bootstrap created it. Secret values suppressed.')
    print('Created roboshop-secrets with random internal credentials and superadmin team@jithendar.com. Values were not printed. Back up this Secret securely.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', default='mk4')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--app-secrets-only', action='store_true', help='Create missing internal app credentials without AWS or controller installation')
    parser.add_argument('--schedule-pull-secrets', action='store_true', help='Install/update a six-hour ECR refresh CronJob using AWS keys in roboshop-secrets')
    parser.add_argument('--pull-secrets-only', action='store_true', help='Refresh ECR pull credentials without installing controllers')
    parser.add_argument('--deploy-apps', action='store_true', help='Deploy the default application catalog after bootstrap')
    parser.add_argument('--accept-new-credentials', action='store_true',
                        help='Create roboshop-secrets even though database volumes already exist; database users must then be reset')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.dry_run:
        if args.app_secrets_only:
            print(f'Context {args.context}: create roboshop-secrets only if absent; preserve existing credentials.')
            return
        print('Create missing internal roboshop-secrets; preserve existing credentials.')
        print(f'Context {args.context}: refresh ECR credentials in managed namespaces (excluding kube-system).')
        if not args.pull_secrets_only:
            print('Then Traefik -> Argo CD -> Argo CD ingress (cert-manager and the add-ons: minikube-infrastructure/)')
        print('Existing Helm releases are reused; no upgrades or ownership adoption.')
        if args.schedule_pull_secrets:
            print('Install six-hour ECR refresh CronJob; requires AWS keys in roboshop-secrets.')
        if args.deploy_apps:
            print('Then deploy the Argo CD application catalog.')
        return
    if args.app_secrets_only:
        if args.pull_secrets_only or args.schedule_pull_secrets or args.deploy_apps:
            parser.error('--app-secrets-only cannot be combined with other actions')
        ensure_app_secrets(args.context, args.accept_new_credentials)
        return
    if args.pull_secrets_only and args.deploy_apps:
        parser.error('--pull-secrets-only cannot be combined with --deploy-apps')
    for tool in (('aws', 'kubectl') if args.pull_secrets_only else ('aws', 'helm', 'kubectl')):
        if not shutil.which(tool):
            raise RuntimeError(f'{tool} is required')
    refresh_pull_secrets(args.context)
    if args.schedule_pull_secrets:
        rendered = subprocess.check_output(['kubectl', 'kustomize', str(ROOT / 'deploy/minikube/ecr-refresh')], text=True)
        docs = list(yaml.safe_load_all(rendered))
        for doc in docs:
            if doc.get('kind') == 'CronJob':
                pod = doc['spec']['jobTemplate']['spec']['template']['spec']
                for env in pod['initContainers'][0]['env']:
                    if env['name'] == 'AWS_REGION':
                        env['value'] = os.getenv('AWS_REGION', 'us-east-1')
                for env in pod['containers'][0]['env']:
                    if env['name'] == 'ECR_REGISTRY':
                        env['value'] = os.getenv('ECR_REGISTRY', '881026659929.dkr.ecr.us-east-1.amazonaws.com')
        subprocess.run(['kubectl', '--context', args.context, '--request-timeout=20s', 'apply', '-f', '-'],
                       input=yaml.safe_dump_all(docs), text=True, check=True)
        print('Six-hour ECR refresh scheduled. It requires AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in roboshop-secrets; verify its first Job.')
    if args.pull_secrets_only:
        return
    ensure_app_secrets(args.context, args.accept_new_credentials)
    kube = ['kubectl', '--context', args.context, '--request-timeout=20s']
    def read(*parts):
        return subprocess.check_output(kube + list(parts), text=True)
    read('get', 'namespace', 'kube-system', '-o', 'name')
    help_text = subprocess.check_output(['helm', 'list', '--help'], text=True)
    flags = ['--all'] if re.search(r'(?m)^\s*(?:-a,\s*)?--all\s', help_text) else []
    for name, repo, version, namespace in CONTROLLERS:
        helm = ['helm', '--kube-context', args.context, '--namespace', namespace]
        releases = json.loads(subprocess.check_output(helm + ['list', *flags, '--filter', f'^{name}$', '-o', 'json'], text=True))
        if releases:
            if releases[0]['status'] != 'deployed':
                raise RuntimeError(f'{name}: existing release is not deployed; inspect helm status. No automatic replacement.')
            print(f'Reusing {name}; version and values preserved.', flush=True)
        else:
            if read('get', 'ingressclass', name, '--ignore-not-found', '-o', 'name').strip():
                raise RuntimeError(f'{name}: existing resources without the expected Helm release; refusing adoption.')
            subprocess.check_call(helm + ['install', name, name, '--repo', repo, '--version', version,
                '--create-namespace', '--values', str(ROOT / 'deploy/minikube' / name / 'values.yaml'),
                '--wait', '--timeout', f'{args.timeout}s'])
        subprocess.check_call(kube + ['-n', namespace, 'rollout', 'status', f'deployment/{name}', f'--timeout={args.timeout}s'])
    # Reuse the existing pinned, Helm-version-compatible Argo installer.
    subprocess.check_call([sys.executable, str(ROOT / 'scripts/argocd/deploy.py'), 'install', '--context', args.context, '--timeout', str(args.timeout)])
    subprocess.check_call(kube + ['apply', '-k', str(ROOT / 'deploy/minikube/argocd')])
    print('Controllers and Argo CD ingress ready. Next: terraform -chdir=minikube-infrastructure apply -var-file=vars/mk4.tfvars '
          '(cert-manager and the add-ons). Public HTTPS still requires host ports 80/443 and the shared certificate.')
    if args.deploy_apps:
        subprocess.check_call([sys.executable, str(ROOT / 'scripts/argocd/deploy.py'), '--context', args.context, '--timeout', str(args.timeout)])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
