"""Mock installer commands without touching a cluster or Docker daemon."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from installer_test_helpers import prepare_source

ROOT = Path(__file__).parent


class TraefikTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        bindir = self.home / '.local/bin'
        bindir.mkdir(parents=True)
        for name in ('helm', 'kubectl', 'docker'):
            p = bindir / name
            p.write_text('#!/bin/sh\n'
                         f'echo "{name} $*" >> "$TEST_LOG"\n'
                         'case "$*" in *"get namespace kube-system"*) exit "${API_EXIT:-0}";; esac\n')
            p.chmod(0o755)
        p = bindir / 'uname'
        p.write_text('#!/bin/sh\necho Darwin\n')
        p.chmod(0o755)
        state = self.home / '.minikube/roboshop-installer'
        state.mkdir(parents=True)
        (state / 'mk4.profile').write_text('mk4-selected\n')
        self.env = {k: v for k, v in os.environ.items() if k not in (
            'KUBE_CONTEXT', 'PROFILE', 'MINIKUBE_HOME', 'TRAEFIK_CHART_VERSION')}
        self.env.update(HOME=str(self.home), TEST_LOG=str(self.home / 'calls'),
                        PATH=f'{bindir}:/usr/bin:/bin')

    def run_script(self, name, **env):
        script = self.home / name
        script.write_text(prepare_source((ROOT / name).read_text()).replace(
            '/usr/local/bin:$HOME/.local/bin:$PATH', '$HOME/.local/bin:$PATH'))
        return subprocess.run(['bash', str(script)], env={**self.env, **env},
                              capture_output=True, text=True)

    def test_selected_profile_and_pinned_chart(self):
        result = self.run_script('deploy-traefik.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.home / 'calls').read_text()
        self.assertIn('--kube-context=mk4-selected upgrade --install traefik', calls)
        self.assertIn('--version 42.0.0', calls)
        self.assertIn('--namespace traefik', calls)
        self.assertIn('get ingressclass traefik', calls)

    def test_failed_api_prevents_install(self):
        result = self.run_script('deploy-traefik.sh', API_EXIT='1', KUBE_CONTEXT='remote')
        self.assertNotEqual(result.returncode, 0)
        calls = (self.home / 'calls').read_text()
        self.assertIn('--context=remote', calls)
        self.assertNotIn('helm ', calls)

    def test_mac_cannot_publish_host_ports(self):
        result = self.run_script('expose-ingress.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Vultr Linux host', result.stderr)
        self.assertFalse((self.home / 'calls').exists())


if __name__ == '__main__':
    unittest.main()
