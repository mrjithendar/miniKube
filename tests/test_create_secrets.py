import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('create_secrets', ROOT / 'scripts/setup/create_secrets.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SecretTests(unittest.TestCase):
    def test_preview_is_offline(self):
        with patch.object(m.subprocess, 'run') as command:
            m.main(['--all', '--ecr', '--apply', '--dry-run'])
            command.assert_not_called()

    def test_pvc_blocks_missing_credentials(self):
        with patch.object(m, 'run', side_effect=[{'metadata': {}}, None, {'items': [{'metadata': {'deletionTimestamp': 'now'}}]}]):
            with self.assertRaisesRegex(RuntimeError, 'Restore the original'):
                m.preflight('mk4', 'wordpress')

    def test_existing_secret_preserved(self):
        with patch.object(m, 'run', return_value={'data': {k: 'original' for k in m.WP_KEYS}}) as command:
            m.create_wordpress('mk4')
            self.assertEqual(command.call_count, 1)

    def test_partial_secret_not_rotated(self):
        with patch.object(m, 'run', return_value={'data': {'admin-password': 'original'}}) as command:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                m.create_wordpress('mk4')
            self.assertEqual(command.call_count, 1)

    def test_creation_uses_stdin_and_distinct_passwords(self):
        with patch.object(m.subprocess, 'run', side_effect=[CompletedProcess([], 0, ''), CompletedProcess([], 0, '')]) as command:
            m.create_wordpress('mk4')
            call = command.call_args
            payload = json.loads(call.kwargs['input'])
            self.assertEqual(set(payload['stringData']), set(m.WP_KEYS))
            self.assertEqual(len(set(payload['stringData'].values())), 4)
            self.assertIn('create', call.args[0])
            for value in payload['stringData'].values():
                self.assertEqual(len(value), 64)
                self.assertNotIn(value, ' '.join(call.args[0]))

    def test_failure_output_redacted(self):
        with patch.object(m.subprocess, 'run', return_value=CompletedProcess([], 1, '', 'SECRET CONTENT')):
            with self.assertRaises(RuntimeError) as raised:
                m.run('mk4', 'create', '-f', '-', document={})
            self.assertNotIn('SECRET CONTENT', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
