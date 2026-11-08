#!/usr/bin/env python3
"""Build Secret istio-ingress/gateway-basic-auth: the gateway's logins, from each application's own login Secret.

  python3 scripts/istio/gateway-basic-auth.py [--context mk4] [--dry-run]

The edge chart (deploy/minikube/istio/edge/values.yaml, basicAuth) lists each login and the Secret it comes from: a
kubernetes.io/basic-auth Secret with keys username and password, the same ones Traefik's middlewares used. Envoy's
basic_auth filter accepts only {SHA} htpasswd lines, so this writes one per login. A missing source Secret gives a line
nobody can match (that path stays locked). Then `terraform apply` in minikube-infrastructure/ passes them to the
gateway. Rerun both after changing a password. Passwords never appear on a command line or in output.
"""
import argparse
import base64
import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / 'deploy/minikube/istio/edge/values.yaml'
TARGET = ('istio-ingress', 'gateway-basic-auth')


def kubectl(context, *args, stdin=None):
    return subprocess.run(['kubectl', '--context', context, *args], input=stdin, capture_output=True, text=True)


def htpasswd_line(user, password):
    if not user or ':' in user or '\n' in user:
        raise ValueError('unusable username')
    digest = base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()
    return f'{user}:{{SHA}}{digest}'


def locked_line():
    # Well-formed (Envoy checks the length) but a random digest: no known password produces it, and the user name
    # is random too.
    return f'locked-{secrets.token_hex(8)}:{{SHA}}{base64.b64encode(secrets.token_bytes(20)).decode()}'


def source_line(context, ref):
    namespace, name = ref.split('/', 1)
    result = kubectl(context, '-n', namespace, 'get', 'secret', name, '-o', 'json')
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout).get('data') or {}
    if 'username' not in data or 'password' not in data:
        return None
    user = base64.b64decode(data['username']).decode()
    password = base64.b64decode(data['password']).decode()
    return htpasswd_line(user, password)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--context', default='mk4')
    parser.add_argument('--dry-run', action='store_true', help='report what would be written, change nothing')
    args = parser.parse_args()

    logins = yaml.safe_load(VALUES.read_text())['basicAuth']
    lines = {}
    for login in logins:
        line = source_line(args.context, login['from'])
        state = 'from ' + login['from'] if line else f"LOCKED ({login['from']} missing or without username/password)"
        print(f"{login['name']}: {state}")
        lines[login['name']] = line or locked_line()

    secret = {
        'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
        'metadata': {'name': TARGET[1], 'namespace': TARGET[0],
                     'labels': {'app.kubernetes.io/part-of': 'istio-edge'}},
        'stringData': lines,
    }
    if args.dry_run:
        print(f'dry run: {TARGET[0]}/{TARGET[1]} not written')
        return 0
    result = kubectl(args.context, 'apply', '-f', '-', stdin=json.dumps(secret))
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1
    print(f'{TARGET[0]}/{TARGET[1]} written. Next: terraform apply in minikube-infrastructure/.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
