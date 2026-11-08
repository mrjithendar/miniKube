"""Create or patch ecr-pull in every non-system namespace; never log credentials or API response bodies."""
import base64
import datetime
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.request

SECRET = 'ecr-pull'
# Kubernetes' own namespaces never run registry images.
DEFAULT_EXCLUDED = 'default,kube-system,kube-public,kube-node-lease'


def target_namespaces(request, excluded):
    """Active namespaces, minus the excluded ones; Terminating namespaces reject writes."""
    items = request('GET', '/api/v1/namespaces')['items']
    return sorted(n['metadata']['name'] for n in items
                  if n['metadata']['name'] not in excluded and n.get('status', {}).get('phase') != 'Terminating')


def use_pull_secret(namespace, request):
    """Add ecr-pull to the default ServiceAccount so pods need no imagePullSecrets of their own."""
    path = f'/api/v1/namespaces/{namespace}/serviceaccounts/default'
    account = request('GET', path)
    if account is None:
        return  # A brand-new namespace gets its default ServiceAccount shortly; the next run attaches it.
    pulls = account.get('imagePullSecrets') or []
    if any(p.get('name') == SECRET for p in pulls):
        return
    request('PATCH', path, {'metadata': {'resourceVersion': account['metadata']['resourceVersion']},
                            'imagePullSecrets': pulls + [{'name': SECRET}]})


def refresh(registry, namespaces, request, authorization):
    decoded = base64.b64decode(authorization, validate=True).decode()
    if not decoded.startswith('AWS:') or len(decoded) <= 4:
        raise ValueError('Invalid ECR authorization token')
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    failures = []
    for namespace in namespaces:
        path = f'/api/v1/namespaces/{namespace}/secrets/{SECRET}'
        try:
            existing = request('GET', path)
            if existing is None:
                config = {'auths': {registry: {'auth': authorization}}}
                request('POST', f'/api/v1/namespaces/{namespace}/secrets', {
                    'apiVersion': 'v1', 'kind': 'Secret', 'type': 'kubernetes.io/dockerconfigjson',
                    'metadata': {'name': SECRET, 'annotations': {'roboshop.ai/ecr-refreshed-at': now}},
                    'data': {'.dockerconfigjson': base64.b64encode(json.dumps(config).encode()).decode()}})
                action = 'created'
            else:
                if existing['type'] != 'kubernetes.io/dockerconfigjson':
                    raise ValueError('Unexpected secret type')
                config = json.loads(base64.b64decode(existing['data']['.dockerconfigjson']))
                config.setdefault('auths', {})[registry] = {'auth': authorization}
                request('PATCH', path, {
                    'metadata': {'resourceVersion': existing['metadata']['resourceVersion'],
                                 'annotations': {'roboshop.ai/ecr-refreshed-at': now}},
                    'data': {'.dockerconfigjson': base64.b64encode(json.dumps(config).encode()).decode()}})
                action = 'refreshed'
            use_pull_secret(namespace, request)
            print(f'{namespace}: {SECRET} {action}', flush=True)
        except Exception as error:
            # Exception text may include response data. Log only its class.
            print(f'{namespace}: refresh failed ({type(error).__name__})', flush=True)
            failures.append(namespace)
    if failures:
        raise RuntimeError('Some namespaces failed; existing credentials were not deleted')


def main():
    token = Path('/var/run/api/token').read_text().strip()
    context = ssl.create_default_context(cafile='/var/run/api/ca.crt')
    def request(method, path, body=None):
        """JSON API call; a GET of a missing object returns None."""
        content_type = 'application/merge-patch+json' if method == 'PATCH' else 'application/json'
        req = urllib.request.Request('https://kubernetes.default.svc' + path,
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': content_type})
        try:
            with urllib.request.urlopen(req, context=context, timeout=20) as response:
                return json.loads(response.read(8 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            if method == 'GET' and error.code == 404:
                return None
            raise
    excluded = {n.strip() for n in os.getenv('EXCLUDED_NAMESPACES', DEFAULT_EXCLUDED).split(',') if n.strip()}
    refresh(os.environ['ECR_REGISTRY'], target_namespaces(request, excluded),
            request, Path('/work/token').read_text().strip())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'ECR refresh failed ({type(error).__name__}); inspect job status and AWS/Kubernetes permissions.', flush=True)
        raise SystemExit(1)
