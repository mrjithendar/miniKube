"""Set up Harbor as the registry for every repository's images; no secret is printed or put on a command line.

Images live in one private project, registry.jithendar.com/jithendar/<image>:latest (README.md "Projects and robot
accounts"). Harbor's own images stay on Docker Hub (deploy-harbor.sh), so Harbor never needs itself to start.

  project            create the private project "jithendar"
  ci-robots          one push+pull robot per GitHub repository; sets its HARBOR_USERNAME and HARBOR_PASSWORD secrets
  pull-secrets       a read-only system robot, written as Secret harbor-pull into each namespace that runs our images
  copy-from-ecr      copy every image's :latest from ECR, so pods can pull before CI first pushes to Harbor

Run from a machine with kubectl access to mk4 (the admin password is read from harbor/harbor-credentials).
ci-robots needs gh, signed in; copy-from-ecr needs docker buildx and "docker login" to both registries.
An existing robot keeps its secret unless --rotate is given.
"""
import argparse
import base64
import getpass
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REGISTRY = 'registry.jithendar.com'
PROJECT = 'jithendar'
PULL_ROBOT = 'k8s-pull'                     # system robot: robot$k8s-pull
PULL_SECRET = 'harbor-pull'
CI_DAYS = 365
# Robot name (robot$jithendar+<name>) -> GitHub repository whose workflows push.
CI_REPOSITORIES = {'moks': 'mrjithendar/moks', 'roboshop-ai': 'mrjithendar/roboshop-ai',
                   'jithendar-com': 'mrjithendar/jithendar.com'}
# Namespaces on mk4 whose pods run images from the project.
NAMESPACES = ('jithendar', 'moks', 'moks-terminals', 'moks-diagnostics', 'roboshop-ai')
ECR = '881026659929.dkr.ecr.us-east-1.amazonaws.com'
# ECR repository -> Harbor repository (same name, in the project).
IMAGES = ('jithendar-com', 'practice-executor', 'practice-sandbox',
          'moks', 'moks-crewai', 'moks-reranker', 'moks-terminal', 'moks-opensearch',
          *(f'roboshop-{s}' for s in ('ai-gateway', 'cart-service', 'catalogue-service', 'currency-service',
                                      'kafka-bridge', 'mcp-server', 'moks', 'payment-service', 'shipping',
                                      'user-service', 'web')))


class Harbor:
    def __init__(self, context, opener=urllib.request.urlopen):
        self.context, self.opener = context, opener
        encoded = subprocess.check_output(['kubectl', '--context=' + context, '--request-timeout=20s', '-n', 'harbor',
                                           'get', 'secret', 'harbor-credentials',
                                           '-o', 'jsonpath={.data.HARBOR_ADMIN_PASSWORD}'], text=True)
        token = base64.b64encode(b'admin:' + base64.b64decode(encoded)).decode()
        self.headers = {'Authorization': 'Basic ' + token, 'Content-Type': 'application/json'}

    def call(self, method, path, body=None):
        """(status, parsed body). 409 Conflict is returned, not raised: it means "already exists"."""
        request = urllib.request.Request(f'https://{REGISTRY}/api/v2.0{path}', method=method, headers=self.headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener(request, timeout=30) as response:
                text = response.read().decode()
                return response.status, json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            if error.code == 409:
                return 409, None
            raise RuntimeError(f'Harbor {method} {path}: HTTP {error.code}') from None

    def robot(self, name, level, permissions, duration, rotate):
        """The robot's secret: new when created or rotated, else None (it exists and Harbor never shows it again)."""
        status, created = self.call('POST', '/robots', {'name': name, 'level': level, 'duration': duration,
                                                        'permissions': permissions})
        if status != 409:
            return created['secret']
        if not rotate:
            return None
        full = f'robot${name}' if level == 'system' else f'robot${PROJECT}+{name}'
        # Listed names carry the robot$ prefix; filter here rather than trust how a query matches the stored name.
        _, found = self.call('GET', '/robots?page_size=100')
        match = [r for r in found or [] if r['name'] == full]
        if not match:
            raise RuntimeError(f'{full} exists but was not found to rotate')
        _, refreshed = self.call('PATCH', f'/robots/{match[0]["id"]}', {})
        return refreshed['secret']


def access(*actions):
    return [{'kind': 'project', 'namespace': PROJECT,
             'access': [{'resource': 'repository', 'action': action} for action in actions]}]


def project(harbor):
    status, _ = harbor.call('POST', '/projects', {'project_name': PROJECT, 'metadata': {'public': 'false'}})
    print(f'Project {PROJECT}: ' + ('exists' if status == 409 else 'created (private)'))


def ci_robots(harbor, rotate, only=()):
    for name, repository in CI_REPOSITORIES.items():
        if only and name not in only:
            continue
        secret = harbor.robot(name, 'project', access('push', 'pull'), CI_DAYS, rotate)
        user = f'robot${PROJECT}+{name}'
        if secret is None:
            print(f'{user} exists; its secret is unchanged (--rotate to replace it in {repository})')
            continue
        for key, value in (('HARBOR_USERNAME', user), ('HARBOR_PASSWORD', secret)):
            subprocess.run(['gh', 'secret', 'set', key, '-R', repository], input=value, text=True, check=True,
                           stdout=subprocess.DEVNULL)
        print(f'{user}: HARBOR_USERNAME and HARBOR_PASSWORD set in {repository} (expires in {CI_DAYS} days)')


def pull_document(namespace, secret):
    auth = base64.b64encode(f'robot${PULL_ROBOT}:{secret}'.encode()).decode()
    config = json.dumps({'auths': {REGISTRY: {'auth': auth}}})
    return {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'kubernetes.io/dockerconfigjson',
            'metadata': {'name': PULL_SECRET, 'namespace': namespace,
                         'labels': {'app.kubernetes.io/part-of': 'harbor'}},
            'data': {'.dockerconfigjson': base64.b64encode(config.encode()).decode()}}


def pull_secrets(harbor, namespaces, rotate, prompt=getpass.getpass):
    # Never expires: an expired pull secret shows up only when a pod restarts and cannot pull.
    secret = harbor.robot(PULL_ROBOT, 'system', access('pull'), -1, rotate)
    if secret is None:
        secret = prompt(f'robot${PULL_ROBOT} exists. Its secret (from your password manager, or rerun with --rotate): ')
        if not secret:
            raise RuntimeError('no secret given')
    kubectl = ['kubectl', '--context=' + harbor.context, '--request-timeout=20s']
    for namespace in namespaces:
        if subprocess.run(kubectl + ['get', 'namespace', namespace], capture_output=True).returncode:
            print(f'{namespace}: no such namespace, skipped (rerun once it exists)')
            continue
        subprocess.run(kubectl + ['apply', '-f', '-'], input=json.dumps(pull_document(namespace, secret)), text=True,
                       check=True, stdout=subprocess.DEVNULL)
        print(f'{namespace}/{PULL_SECRET}: applied')
    print(f'Keep the robot${PULL_ROBOT} secret in your password manager: new namespaces and restores need it.')


def copy_from_ecr(images=IMAGES):
    """Server-side copy of every platform of :latest; nothing is pulled into the local Docker."""
    if not shutil.which('docker'):
        raise RuntimeError('docker (with buildx) is required')
    missing = []
    for image in images:
        source, target = f'{ECR}/{image}:latest', f'{REGISTRY}/{PROJECT}/{image}:latest'
        result = subprocess.run(['docker', 'buildx', 'imagetools', 'create', '--tag', target, source],
                                capture_output=True, text=True)
        if result.returncode:
            missing.append(image)
            print(f'{image}: not copied ({(result.stderr.strip().splitlines() or ["?"])[-1]})')
        else:
            print(f'{image}: copied to {target}')
    if missing:
        print('Not copied: ' + ', '.join(missing) + '. Images that were never in ECR arrive with their next CI build.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('project', 'ci-robots', 'pull-secrets', 'copy-from-ecr'))
    parser.add_argument('--context', default='mk4')
    parser.add_argument('--rotate', action='store_true', help='give existing robots a new secret')
    parser.add_argument('--namespace', action='append', help=f'pull-secrets target (default: {", ".join(NAMESPACES)})')
    parser.add_argument('--robot', action='append', choices=sorted(CI_REPOSITORIES), help='ci-robots: only these')
    args = parser.parse_args(argv)
    if args.command == 'copy-from-ecr':
        return copy_from_ecr()
    harbor = Harbor(args.context)
    if args.command == 'project':
        project(harbor)
    elif args.command == 'ci-robots':
        ci_robots(harbor, args.rotate, args.robot or ())
    else:
        pull_secrets(harbor, args.namespace or NAMESPACES, args.rotate)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(f'ERROR: {error}')
