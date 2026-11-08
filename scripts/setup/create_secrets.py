#!/usr/bin/env python3
"""Create missing Minikube installation credentials; never rotate existing secrets."""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TARGETS = {
    'roboshop': ('roboshop-ai', 'roboshop-secrets'),
    'monitoring': ('monitoring', 'monitoring-auth'),
    'wordpress': ('jithendar', 'wordpress-credentials'),
    'jfrog': ('jfrog', 'jfrog-bootstrap'),
    'harbor': ('harbor', 'harbor-credentials'),
}
WP_KEYS = ('database-password', 'database-root-password', 'redis-password', 'admin-password')


def module(relative):
    spec = importlib.util.spec_from_file_location('secret_helper', ROOT / relative)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(context, *args, document=None):
    # Capture stderr too: Kubernetes errors can echo submitted Secret objects.
    result = subprocess.run(['kubectl', '--context', context, '--request-timeout=20s', *args],
        input=json.dumps(document) if document is not None else None,
        text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Kubernetes operation failed; check context/RBAC. Output suppressed to protect credentials.')
    return json.loads(result.stdout) if result.stdout.strip().startswith('{') else None


def preflight(context, target):
    namespace, name = TARGETS[target]
    ns = run(context, 'get', 'namespace', namespace, '--ignore-not-found', '-o', 'json')
    if not ns:
        return
    current = run(context, '-n', namespace, 'get', 'secret', name, '--ignore-not-found', '-o', 'json')
    if current:
        return
    # A missing Secret does not mean a fresh installation. Generated replacement
    # credentials cannot decrypt existing data or log in to initialized databases.
    pvcs = run(context, '-n', namespace, 'get', 'pvc', '-o', 'json')
    if pvcs.get('items'):
        raise RuntimeError(f'{namespace}/{name} missing but PVCs exist. Restore the original Secret from backup; no credentials generated.')


def create_wordpress(context):
    namespace, name = TARGETS['wordpress']
    current = run(context, '-n', namespace, 'get', 'secret', name, '--ignore-not-found', '-o', 'json')
    if current:
        if not all(current.get('data', {}).get(key) for key in WP_KEYS):
            raise RuntimeError('wordpress-credentials is incomplete; preserved. Restore original missing keys.')
        print('Reusing jithendar/wordpress-credentials.')
        return
    run(context, 'create', '-f', '-', document={
        'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
        'metadata': {'namespace': namespace, 'name': name},
        'stringData': {key: secrets.token_hex(32) for key in WP_KEYS},
    })
    print('Created jithendar/wordpress-credentials; values not displayed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', default='mk4')
    parser.add_argument('--services', nargs='+', choices=TARGETS, default=['roboshop', 'monitoring', 'wordpress'],
                        help='Default: roboshop monitoring wordpress; optionally include jfrog or harbor')
    parser.add_argument('--all', action='store_true', help='Include JFrog and Harbor installation credentials')
    parser.add_argument('--apply', action='store_true', help='Create missing credentials (default: offline preview)')
    parser.add_argument('--dry-run', action='store_true', help='Offline preview, even with --apply')
    parser.add_argument('--ecr', action='store_true', help='Also refresh ECR pull secrets using current AWS credentials')
    args = parser.parse_args(argv)
    targets = list(TARGETS) if args.all else list(dict.fromkeys(args.services))
    for target in targets:
        print('/'.join(TARGETS[target]) + ': preserve existing; create only if absent')
    print('TLS, Argo CD, Helm and ECK secrets remain controller-managed. External API keys must be supplied by their providers.')
    if args.ecr:
        print('ECR: refresh pull tokens in repository-managed namespaces (requires AWS authentication).')
    if not args.apply or args.dry_run:
        print('Preview only. Add --apply to execute. No cluster or AWS calls made.')
        return
    # Check every selected namespace before any writes, including terminating PVCs.
    for target in targets:
        preflight(args.context, target)
    for target in targets:
        namespace, _ = TARGETS[target]
        if not run(args.context, 'get', 'namespace', namespace, '--ignore-not-found', '-o', 'json'):
            run(args.context, 'create', 'namespace', namespace)
        if target == 'roboshop':
            module('scripts/argocd/bootstrap.py').ensure_app_secrets(args.context)
        elif target == 'monitoring':
            module('deploy/minikube/observability/credentials.py').Credentials(args.context).ensure()
        elif target == 'wordpress':
            create_wordpress(args.context)
        elif target == 'jfrog':
            module('deploy/minikube/jfrog/bootstrap.py').ensure(args.context)
        elif target == 'harbor':
            module('deploy/minikube/harbor/credentials.py').ensure(args.context)
    if args.ecr:
        module('scripts/argocd/bootstrap.py').refresh_pull_secrets(args.context)
    print('Done. No workloads restarted and no existing credentials rotated.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print('ERROR: ' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
