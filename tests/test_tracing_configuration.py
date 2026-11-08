from pathlib import Path
import subprocess
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from repos import APP_REPO, MINIKUBE, checkout  # noqa: E402
APP = checkout(APP_REPO)  # roboshop-ai: microservices and their manifests
class Config(unittest.TestCase):
 def test_collector_owns_its_configuration_in_observability(self):
  data=yaml.safe_load((APP/'argocd-eks/apps/roboshop/roboshop-config-configmap.yaml').read_text())['data']
  self.assertNotIn('config.yaml',data)
  self.assertEqual(data['OTEL_EXPORTER_OTLP_TRACES_ENDPOINT'],'http://otel-collector.observability.svc.cluster.local:4318/v1/traces')
  chart=ROOT/'deploy/minikube/observability/otel-collector'
  docs=[d for d in yaml.safe_load_all(subprocess.check_output(['helm','template','otel-collector',str(chart),'-n','observability'],text=True)) if d]
  config=next(d for d in docs if d['kind']=='ConfigMap' and d['metadata']['name']=='otel-collector-config')
  self.assertEqual(config['data'],{'config.yaml':(chart/'collector.yaml').read_text()})
  alias=yaml.safe_load((chart/'templates/roboshop-ai-otel-collector-service-minikube.yaml').read_text())
  self.assertEqual((alias['metadata']['namespace'],alias['spec']['type']),('roboshop-ai','ExternalName'))
 def test_new_exporters_receive_shared_configuration(self):
  for service in ('currency-service','kafka-bridge'):
   docs=(yaml.safe_load(p.read_text()) for p in (APP/f'services/{service}').glob('*.yaml'))
   containers=[c for d in docs if d['kind'] in ('Deployment','StatefulSet') for c in d['spec']['template']['spec']['containers'] if c['name']==service]
   self.assertEqual(len(containers),1)
   self.assertIn({'configMapRef':{'name':'roboshop-config'}},containers[0]['envFrom'])
