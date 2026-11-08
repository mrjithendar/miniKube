import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('credentials', Path(__file__).resolve().parents[2] / 'deploy/minikube/observability/credentials.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def secret(password, grafana=False):
    keys = ('admin-user', 'admin-password') if grafana else ('username', 'password')
    return {'type': 'kubernetes.io/basic-auth', 'data': {
        keys[0]: base64.b64encode(b'admin').decode(),
        keys[1]: base64.b64encode(password.encode()).decode()}}


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.manager = module.Credentials('test-context')

    def test_existing_shared_secret_is_never_rotated(self):
        with patch.object(self.manager, 'get', return_value=secret('existing')) as get, patch.object(module.subprocess, 'run') as run:
            self.manager.ensure()
            get.assert_called_once_with('monitoring-auth')
            run.assert_not_called()

    def test_adopts_grafana_before_prometheus(self):
        with patch.object(self.manager, 'get', side_effect=[None, secret('grafana-password', True)]), patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.manager.ensure()
            payload = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(payload['stringData']['password'], 'grafana-password')
            self.assertEqual(payload['metadata']['name'], 'monitoring-auth')
            self.assertNotIn('grafana-password', str(run.call_args.args))

    def test_adopts_prometheus_if_grafana_absent(self):
        with patch.object(self.manager, 'get', side_effect=[None, None, secret('prom-password')]), patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.manager.ensure()
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['stringData']['password'], 'prom-password')

    def test_fresh_install_generates_password(self):
        with patch.object(self.manager, 'get', return_value=None), patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.manager.ensure()
            self.assertEqual(len(json.loads(run.call_args.kwargs['input'])['stringData']['password']), 64)

    def test_grafana_sync_uses_stdin_not_arguments(self):
        with patch.object(self.manager, 'get', return_value=secret('shared-password')), patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.manager.sync_grafana()
            self.assertEqual(run.call_args.kwargs['input'], 'shared-password\n')
            self.assertNotIn('shared-password', str(run.call_args.args))
            self.assertIn('--password-from-stdin', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
