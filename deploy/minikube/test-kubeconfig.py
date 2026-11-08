"""Offline checks: never connect to SSH or Kubernetes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('kubeconfig.sh').resolve()

class KubeconfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.config.write_text('original tunnel config')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        remote = {'port': '32769', 'config': {'current-context': 'mk4',
            'contexts': [{'name': 'mk4', 'context': {'cluster': 'mk4', 'user': 'mk4'}}],
            'users': [{'name': 'mk4', 'user': {'token': 'test-only'}}],
            'clusters': [{'name': 'mk4', 'cluster': {'server': 'https://192.168.49.2:8443',
            'certificate-authority-data': 'ca', 'proxy-url': 'http://old-proxy'}}]}}
        (self.root / 'export.json').write_text(json.dumps(remote))
        mocks = {'ssh': 'cat >/dev/null; [ "${SSH_FAIL:-0}" = 0 ] || exit 1; cat "$TEST_SOURCE"',
                 'kubectl': 'echo called >> "$TEST_CALLS"; case "$*" in *"config view"*) echo \'{"clusters":[{"name":"eks","cluster":{"server":"https://example.test"}}],"contexts":[{"name":"eks","context":{"cluster":"eks"}}]}\'; exit 0;; esac; exit "${API_FAIL:-0}"'}
        for name, body in mocks.items():
            p = self.bin / name
            p.write_text('#!/bin/sh\n'+body+'\n')
            p.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin)+':'+os.environ['PATH'],
            CONFIG=str(self.config), TEST_SOURCE=str(self.root/'export.json'),
            TEST_CALLS=str(self.root/'calls'), PROFILE='mk4', API_HOST='jithendar.com',
            VULTR_HOST='root@jithendar.com')

    def run_script(self, **env):
        return subprocess.run(['sh', str(SCRIPT)], env={**self.env, **env}, capture_output=True, text=True)

    def test_direct_export(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads(self.config.read_text())
        self.assertEqual(merged['current-context'], 'mk4')
        self.assertIn('eks', [entry['name'] for entry in merged['contexts']])
        self.assertEqual(len(list(self.root.glob('config.json.backup-*'))), 1)
        cluster = next(entry['cluster'] for entry in merged['clusters'] if entry['name'] == 'mk4')
        self.assertEqual(cluster['server'], 'https://jithendar.com:32769')
        self.assertEqual(cluster['tls-server-name'], '192.168.49.2')
        self.assertFalse(cluster['insecure-skip-tls-verify'])
        self.assertNotIn('proxy-url', cluster)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_failed_ssh_preserves_config_and_does_not_call_api(self):
        self.assertNotEqual(self.run_script(SSH_FAIL='1').returncode, 0)
        self.assertEqual(self.config.read_text(), 'original tunnel config')
        self.assertFalse((self.root/'calls').exists())

    def test_failed_api_preserves_config(self):
        self.assertNotEqual(self.run_script(API_FAIL='1').returncode, 0)
        self.assertEqual(self.config.read_text(), 'original tunnel config')

if __name__ == '__main__':
    unittest.main()
