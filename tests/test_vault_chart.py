"""Validate the real Helm rendering without contacting Kubernetes."""
import sys
import unittest
from pathlib import Path
import yaml

RENDERED = Path(sys.argv.pop(1)) if __name__ == '__main__' and len(sys.argv) > 1 else None


@unittest.skipUnless(RENDERED, 'Pass the helm template output path')
class VaultChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = [doc for doc in yaml.safe_load_all(RENDERED.read_text()) if doc]
        cls.stateful = next(d for d in cls.docs if d['kind'] == 'StatefulSet')

    def test_single_persistent_non_dev_server(self):
        self.assertEqual(self.stateful['spec']['replicas'], 1)
        self.assertEqual(self.stateful['spec']['volumeClaimTemplates'][0]['spec']['resources']['requests']['storage'], '10Gi')
        containers = self.stateful['spec']['template']['spec']['containers']
        self.assertEqual(containers[0]['image'], 'hashicorp/vault:2.0.4')
        self.assertNotIn('-dev', str(containers[0]['args']))
        self.assertNotIn('VAULT_DEV_ROOT_TOKEN_ID', str(containers))

    def test_sealed_ui_access_without_false_readiness(self):
        ui = next(d for d in self.docs if d['kind'] == 'Service' and d['metadata']['name'] == 'vault-ui')
        self.assertTrue(ui['spec']['publishNotReadyAddresses'])
        self.assertEqual(ui['spec']['type'], 'ClusterIP')
        probe = self.stateful['spec']['template']['spec']['containers'][0]['readinessProbe']
        self.assertNotIn('sealedcode=200', str(probe))
        self.assertNotIn('uninitcode=200', str(probe))

    def test_no_injector_or_embedded_credentials(self):
        self.assertFalse(any(d['kind'] == 'MutatingWebhookConfiguration' for d in self.docs))
        self.assertFalse(any(d['kind'] == 'Secret' for d in self.docs))


if __name__ == '__main__':
    unittest.main()
