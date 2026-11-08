"""Offline installer checks: no Docker, downloads, or real cluster changes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from installer_test_helpers import prepare_source

SCRIPT = Path(__file__).with_name('install.sh').resolve()

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        mocks = {
            'sysctl': 'case "$*" in *max_user_instances*) echo 1024;; *max_user_watches*) echo 1048576;; esac',
            'uname': 'if [ "$1" = -s ]; then echo Linux; else echo x86_64; fi',
            'docker': '''case "$*" in
*MemTotal*) echo 4093640704;;
*NCPU*) echo 2;;
*OSType*) echo linux;;
port*) if [ "${NO_PUBLIC_PORT:-0}" != 1 ]; then echo 0.0.0.0:32811; fi;;
esac''',
            'minikube': '''echo "$*" >> "$TEST_CALLS"
case "$*" in start*) exit "${TEST_START_EXIT:-0}";; esac''',
            'kubectl': """case "$*" in
*"config view"*) echo '{"clusters":[{"name":"test","cluster":{"server":"https://localhost:32811","certificate-authority-data":"test-ca"}}],"users":[{"name":"test","user":{"client-certificate-data":"test-cert","client-key-data":"test-key"}}]}' ;;
*"-o name"*) echo node/test;; esac""",
        }
        for name, body in mocks.items():
            f = self.bin / name
            f.write_text('#!/bin/sh\n' + body + '\n')
            f.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if k not in (
            'NODES', 'MEMORY', 'CPUS', 'PROFILE', 'MINIKUBE_HOME', 'PUBLIC_IP', 'PUBLIC_HOST')}
        self.env.update(HOME=str(self.root), INSTALL_DIR=str(self.bin),
                        PATH=str(self.bin) + ':' + os.environ['PATH'],
                        TEST_CALLS=str(self.root / 'calls'), NODES='1')

    def run_script(self, action='start', **overrides):
        script = self.root / 'install.sh'
        source = prepare_source(SCRIPT.read_text())
        source = source.replace('$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)', str(SCRIPT.parent))
        # Host limits have a separate suite; keep this lifecycle fixture offline.
        source = source.replace(f'bash "{SCRIPT.parent}/host-limits.sh"', 'true')
        script.write_text(source)
        return subprocess.run(['bash', str(script), action],
                              env={**self.env, **overrides}, text=True,
                              capture_output=True)

    def calls(self):
        p = self.root / 'calls'
        return p.read_text() if p.exists() else ''

    def config(self, profile, nodes=3):
        p = self.root / '.minikube/profiles' / profile / 'config.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dict(Driver='docker', CPUs=2, Memory=2048,
                                    Nodes=[{'Name': str(i)} for i in range(nodes)])))
        return p

    def test_small_host_defaults(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--nodes=1', self.calls())
        self.assertIn('--memory=2560', self.calls())

    def test_overallocation_rejected_before_mutation(self):
        result = self.run_script(NODES='4', MEMORY='2048')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), '')

    def test_existing_cluster_preserved_and_mapping_reused(self):
        old = self.config('mk4')
        original = old.read_text()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--profile=mk4 stop', self.calls())
        self.assertIn('--profile=mk4-fit-1n-2560m-2c', self.calls())
        self.assertNotIn('delete', self.calls())
        self.assertEqual(old.read_text(), original)
        self.assertEqual(self.run_script('status').returncode, 0)
        self.assertIn('--profile=mk4-fit-1n-2560m-2c status', self.calls())

    def test_direct_access_export(self):
        result = self.run_script(PUBLIC_IP='203.0.113.10')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--listen-address=0.0.0.0', self.calls())
        self.assertIn('--ports=0.0.0.0:32811:8443', self.calls())
        self.assertIn('--apiserver-ips=203.0.113.10', self.calls())
        output = self.root / '.kube/mk4-direct.json'
        config = json.loads(output.read_text())
        self.assertEqual(config['clusters'][0]['cluster']['server'], 'https://203.0.113.10:32811')
        self.assertFalse(config['clusters'][0]['cluster']['insecure-skip-tls-verify'])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('test-key', result.stdout)

    def test_domain_export(self):
        result = self.run_script(PUBLIC_HOST='jithendar.com')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--apiserver-names=jithendar.com', self.calls())
        self.assertNotIn('--apiserver-ips=', self.calls())
        config = json.loads((self.root / '.kube/mk4-direct.json').read_text())
        cluster = config['clusters'][0]['cluster']
        self.assertEqual(cluster['server'], 'https://jithendar.com:32811')
        self.assertFalse(cluster['insecure-skip-tls-verify'])

    def test_invalid_domain_and_conflicting_inputs(self):
        for host in ('https://jithendar.com', 'jithendar.com:443', '-bad.example', 'a..example', '1.2.3.4'):
            self.assertNotEqual(self.run_script(PUBLIC_HOST=host).returncode, 0)
        self.assertNotEqual(self.run_script(PUBLIC_HOST='jithendar.com', PUBLIC_IP='203.0.113.10').returncode, 0)
        self.assertEqual(self.calls(), '')

    def test_restart_retains_direct_access(self):
        self.assertEqual(self.run_script(PUBLIC_HOST='jithendar.com').returncode, 0)
        (self.root / 'calls').write_text('')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--apiserver-names=jithendar.com', self.calls())

    def test_failed_export_does_not_select_new_profile(self):
        result = self.run_script(PUBLIC_HOST='jithendar.com', NO_PUBLIC_PORT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '.minikube/roboshop-installer/mk4.profile').exists())

    def test_invalid_or_conflicting_fixed_port(self):
        for port in ('0', '80', '65536', 'abc'):
            result = self.run_script(PUBLIC_HOST='jithendar.com', API_PORT=port)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.calls(), '')
        result = self.run_script(PUBLIC_HOST='jithendar.com', API_PORT='32812')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No cluster changed', result.stderr)
        self.assertEqual(self.calls(), '')

    def test_invalid_public_address(self):
        self.assertNotEqual(self.run_script(PUBLIC_IP='999.2.3.4').returncode, 0)
        self.assertEqual(self.calls(), '')

    def test_corrupt_config_not_modified(self):
        self.config('mk4').write_text('{broken')
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(self.calls(), '')

    def test_failure_keeps_exit_code_without_mapping(self):
        result = self.run_script(TEST_START_EXIT='42')
        self.assertEqual(result.returncode, 42)
        self.assertFalse((self.root / '.minikube/roboshop-installer/mk4.profile').exists())

if __name__ == '__main__':
    unittest.main()
