"""Create harbor/harbor-credentials once, without printing it; never rotate it.

Every credential the Harbor chart would otherwise generate at render time lives here (values.yaml names each key),
as do the PostgreSQL and Redis passwords for manifests/harbor-postgres-* and manifests/harbor-redis-*.
Back it up with the database: secretKey encrypts stored registry credentials, and the database password is
written into the data directory on first start. README.md "Backup and restore".
"""
import argparse
import json
import secrets
import shutil
import string
import subprocess

NAMESPACE, NAME = 'harbor', 'harbor-credentials'
REGISTRY_USER = 'harbor_registry_user'      # registry.credentials.username in values.yaml
KEYS = ('HARBOR_ADMIN_PASSWORD', 'secretKey', 'secret', 'CSRF_KEY', 'JOBSERVICE_SECRET', 'REGISTRY_HTTP_SECRET',
        'REGISTRY_PASSWD', 'REGISTRY_HTPASSWD', 'password', 'REDIS_PASSWORD')


def admin_password():
    # Harbor's password rule: 8-128 characters with an upper-case letter, a lower-case letter and a digit.
    while True:
        value = secrets.token_urlsafe(24)
        if all(any(c in group for c in value) for group in (string.ascii_uppercase, string.ascii_lowercase, string.digits)):
            return value


def htpasswd(user, password):
    """bcrypt entry for the registry's htpasswd file; the password travels on stdin, never argv."""
    result = subprocess.run(['htpasswd', '-niB', '-C', '10', user], input=password + '\n',
                            text=True, capture_output=True, check=True)
    return result.stdout.strip()


def generate():
    registry_password = secrets.token_hex(16)
    return {
        'HARBOR_ADMIN_PASSWORD': admin_password(),
        'secretKey': secrets.token_hex(8),             # exactly 16 characters: Harbor's AES key
        'secret': secrets.token_hex(8),                # core secret
        'CSRF_KEY': secrets.token_hex(16),             # 32 characters
        'JOBSERVICE_SECRET': secrets.token_hex(8),
        'REGISTRY_HTTP_SECRET': secrets.token_hex(8),
        'REGISTRY_PASSWD': registry_password,
        'REGISTRY_HTPASSWD': htpasswd(REGISTRY_USER, registry_password),
        'password': secrets.token_hex(24),             # PostgreSQL user harbor
        'REDIS_PASSWORD': secrets.token_hex(24),       # hex: it is embedded in redis:// URLs unescaped
    }


def ensure(context):
    command = ['kubectl', '--context=' + context, '--request-timeout=20s', '-n', NAMESPACE]

    def read(args):
        return subprocess.check_output(command + args, text=True).strip()
    current = read(['get', 'secret', NAME, '--ignore-not-found', '-o', 'json'])
    if current:
        if not all(json.loads(current).get('data', {}).get(k) for k in KEYS):
            raise RuntimeError(f'Existing {NAMESPACE}/{NAME} is incomplete. Restore it from backup; do not rotate secretKey or database passwords.')
        print(f'Reusing {NAMESPACE}/{NAME}.')
        return
    if json.loads(read(['get', 'pvc', '-o', 'json']))['items']:
        raise RuntimeError(f'Harbor PVCs exist without {NAME}. Restore the original Secret from backup.')
    if not shutil.which('htpasswd'):
        raise RuntimeError('htpasswd not found: install apache2-utils (Debian/Ubuntu) or httpd-tools (RHEL); macOS ships it.')
    document = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                'metadata': {'name': NAME, 'namespace': NAMESPACE, 'labels': {'app.kubernetes.io/part-of': 'harbor'}},
                'stringData': generate()}
    subprocess.run(command + ['create', '-f', '-'], input=json.dumps(document), text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f'Created {NAMESPACE}/{NAME}; values were not printed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    ensure(parser.parse_args().context)
