"""Create shared monitoring credentials and synchronize Grafana without logging passwords."""

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
import base64
import json
import secrets
import subprocess
import sys


class Credentials:
    def __init__(self, context):
        self.command = ['kubectl', '--context=' + context, '--request-timeout=30s', '-n', 'monitoring']

    def get(self, name):
        result = subprocess.run(self.command + ['get', 'secret', name, '--ignore-not-found', '-o', 'json'],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('Could not read Secret ' + name + '; check cluster access.')
        return json.loads(result.stdout) if result.stdout.strip() else None

    @staticmethod
    def decode(secret, user_key, password_key):
        try:
            data = secret['data']
            user = base64.b64decode(data[user_key], validate=True).decode()
            password = base64.b64decode(data[password_key], validate=True).decode()
        except (KeyError, ValueError, UnicodeError):
            raise RuntimeError('Credential Secret is missing valid username/password keys.') from None
        if user != 'admin' or not password or '\n' in password or '\r' in password:
            raise RuntimeError('Shared credentials require username admin and a nonempty single-line password.')
        return user, password

    def ensure(self):
        current = self.get('monitoring-auth')
        if current:
            if current.get('type') != 'kubernetes.io/basic-auth':
                raise RuntimeError('monitoring-auth must have type kubernetes.io/basic-auth.')
            self.decode(current, 'username', 'password')
            print('Using existing monitoring/monitoring-auth (password unchanged).')
            return
        user, password = 'admin', None
        for name, user_key, password_key in (
                ('grafana', 'admin-user', 'admin-password'), ('prometheus-auth', 'username', 'password')):
            old = self.get(name)
            if old:
                user, password = self.decode(old, user_key, password_key)
                print('Adopting stored credentials from monitoring/' + name + '.')
                break
        if password is None:
            password = secrets.token_hex(32)
        payload = {'apiVersion': 'v1', 'kind': 'Secret',
                   'metadata': {'name': 'monitoring-auth', 'namespace': 'monitoring'},
                   'type': 'kubernetes.io/basic-auth',
                   'stringData': {'username': user, 'password': password}}
        result = subprocess.run(self.command + ['create', '-f', '-'], input=json.dumps(payload),
                                capture_output=True, text=True)
        if result.returncode:
            # A concurrently running installer may have created it; never overwrite it.
            current = self.get('monitoring-auth')
            if not current or current.get('type') != 'kubernetes.io/basic-auth':
                raise RuntimeError('Could not create monitoring-auth; check Kubernetes permissions.')
            self.decode(current, 'username', 'password')
        print('Shared monitoring/monitoring-auth is ready; password not displayed.')

    def sync_grafana(self):
        current = self.get('monitoring-auth')
        if not current:
            raise RuntimeError('monitoring-auth is missing; run the installer first.')
        _, password = self.decode(current, 'username', 'password')
        result = subprocess.run(self.command + [
            'exec', '-i', 'deployment/grafana', '-c', 'grafana', '--',
            '/usr/share/grafana/bin/grafana', 'cli', '--homepath', '/usr/share/grafana',
            '--config', '/etc/grafana/grafana.ini',
            '--configOverrides', 'cfg:default.paths.data=/var/lib/grafana',
            'admin', 'reset-admin-password', '--user-id', '1', '--password-from-stdin'],
            input=password + '\n', capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('Grafana admin synchronization failed. Check pod readiness, CLI availability and the original administrator (user ID 1). Credentials were not printed.')
        print('Grafana administrator password synchronized with monitoring-auth.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['ensure', 'sync-grafana'])
    parser.add_argument('--context', required=True)
    args = parser.parse_args()
    try:
        manager = Credentials(args.context)
        if args.action == 'ensure':
            manager.ensure()
        else:
            manager.sync_grafana()
    except (RuntimeError, ValueError, OSError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
