"""Create persistent installation credentials without printing secrets."""

# Portable operator command; imports remain usable by isolated tests.
if __name__ == '__main__':
    import sys as _host_sys
    if not any(arg in ('--help', '-h') for arg in _host_sys.argv[1:]):
        import subprocess as _host_subprocess
        from pathlib import Path as _HostPath
        _host_gate = _HostPath(__file__).resolve().parent / '../../..' / 'scripts/lib/require-centos.sh'
        if _host_subprocess.call(['bash', str(_host_gate)]):
            raise SystemExit(1)

import argparse
import json
import secrets
import subprocess

KEYS = ('master-key', 'join-key', 'admin-password', 'bootstrap.creds', 'postgres-password', 'password')


def ensure(context):
    command = ['kubectl', '--context=' + context, '--request-timeout=20s', '-n', 'jfrog']
    def read(args):
        return subprocess.check_output(command + args, text=True).strip()
    current = read(['get', 'secret', 'jfrog-bootstrap', '--ignore-not-found', '-o', 'json'])
    if current:
        if not all(json.loads(current).get('data', {}).get(k) for k in KEYS):
            raise RuntimeError('Existing jfrog-bootstrap is incomplete. Restore it; do not rotate database or master keys.')
        print('Reusing JFrog credentials.')
        return
    if json.loads(read(['get', 'pvc', '-o', 'json']))['items']:
        raise RuntimeError('JFrog PVCs exist without credentials. Restore the original jfrog-bootstrap Secret.')
    password = secrets.token_urlsafe(36)
    values = {'master-key': secrets.token_hex(32), 'join-key': secrets.token_hex(32),
              'admin-password': password, 'bootstrap.creds': 'admin@127.0.0.1=' + password,
              'postgres-password': secrets.token_urlsafe(36), 'password': secrets.token_urlsafe(36)}
    document = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'jfrog-bootstrap', 'namespace': 'jfrog'},
                'type': 'Opaque', 'stringData': values}
    subprocess.run(command + ['create', '-f', '-'], input=json.dumps(document), text=True, check=True, stdout=subprocess.DEVNULL)
    print('Created JFrog credentials; values were not printed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    ensure(parser.parse_args().context)
