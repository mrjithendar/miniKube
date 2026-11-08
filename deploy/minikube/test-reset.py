"""Test reset with an isolated HOME and fake Minikube; never touch real clusters."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('reset.sh').resolve()

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        binary = self.home / '.local/bin/minikube'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$HOME/calls"\nexit "${TEST_EXIT:-0}"\n')
        binary.chmod(0o755)
        (self.home / '.minikube/profiles/mk4').mkdir(parents=True)
        (self.home / '.kube').mkdir()
        self.export = self.home / '.kube/mk4-direct.json'
        self.export.write_text('private export')
        self.unrelated = self.home / '.kube/config'
        self.unrelated.write_text('unrelated cluster')
        self.env = dict(os.environ, HOME=str(self.home))
        self.env.pop('MINIKUBE_HOME', None)

    def run_reset(self, **overrides):
        return subprocess.run(['bash', str(SCRIPT)], env={**self.env, **overrides},
                              text=True, capture_output=True)

    def test_reset_removes_only_matching_exports(self):
        result = self.run_reset()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / 'calls').read_text(), 'delete --all --purge\n')
        self.assertFalse(self.export.exists())
        self.assertEqual(self.unrelated.read_text(), 'unrelated cluster')

    def test_failed_delete_preserves_exports(self):
        result = self.run_reset(TEST_EXIT='9')
        self.assertEqual(result.returncode, 9)
        self.assertTrue(self.export.exists())

    def test_custom_home_rejected(self):
        self.assertNotEqual(self.run_reset(MINIKUBE_HOME='/').returncode, 0)
        self.assertFalse((self.home / 'calls').exists())

if __name__ == '__main__':
    unittest.main()
