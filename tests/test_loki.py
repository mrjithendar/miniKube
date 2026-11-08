from pathlib import Path
import subprocess
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]

class LokiTests(unittest.TestCase):
    def test_render_and_boundaries(self):
        docs=[d for d in yaml.safe_load_all(subprocess.check_output(['helm','template','loki',str(ROOT/'deploy/minikube/observability/loki'),'-n','monitoring'],text=True)) if d]
        deployments=[d for d in docs if d['kind']=='Deployment']
        self.assertEqual({d['metadata']['name'] for d in deployments},{'loki','alloy'})
        self.assertFalse(any(d['kind'] in ('IngressRoute','Role','RoleBinding','Secret') for d in docs))
        for d in deployments:
            self.assertEqual(d['spec']['replicas'],1)
            pod=d['spec']['template']['spec']
            self.assertTrue(pod['securityContext']['runAsNonRoot'])
            self.assertFalse(any('hostPath' in v for v in pod['volumes']))
        roles=[d for d in docs if d['kind']=='ClusterRole']
        self.assertEqual([r['metadata']['name'] for r in roles],['alloy-logs'])
        self.assertTrue(all(set(rule['resources'])<={'pods','pods/log'} for r in roles for rule in r['rules']))
        self.assertTrue(all(set(rule['verbs'])<={'get','list','watch'} for r in roles for rule in r['rules']))
        binding=next(d for d in docs if d['kind']=='ClusterRoleBinding')
        self.assertEqual(binding['subjects'],[{'kind':'ServiceAccount','name':'alloy','namespace':'monitoring'}])

    def test_public_paths_are_push_and_the_reads_moks_calls(self):
        docs=[d for d in yaml.safe_load_all(subprocess.check_output(['helm','template','loki',str(ROOT/'deploy/minikube/observability/loki'),'-n','monitoring'],text=True)) if d]
        self.assertFalse(any(d['kind'] in ('Ingress','Middleware') for d in docs))
        route=next(d for d in docs if d['kind']=='VirtualService' and d['metadata']['name']=='loki')
        # A delegate of the gateway's routing table: no hosts or gateways of its own.
        self.assertNotIn('hosts',route['spec']); self.assertNotIn('gateways',route['spec'])
        (http,)=route['spec']['http']
        matches={next(iter(m['uri'].items())) for m in http['match']}
        self.assertEqual(matches,{('exact','/loki/api/v1/push'),('exact','/loki/api/v1/query_range'),('exact','/loki/api/v1/labels'),
                                  ('exact','/loki/api/v1/format_query'),('prefix','/loki/api/v1/label')})
        self.assertEqual(http['route'][0]['destination'],{'host':'loki.monitoring.svc.cluster.local','port':{'number':3100}})

    def test_push_and_read_have_separate_logins_on_the_gateway(self):
        edge=yaml.safe_load((ROOT/'deploy/minikube/istio/edge/values.yaml').read_text())
        logins={l['name']:l for l in edge['basicAuth']}
        self.assertEqual(logins['loki-push']['prefix'],'/loki/api/v1/push')
        self.assertEqual(logins['loki-push']['from'],'monitoring/loki-push-auth')
        self.assertEqual((logins['loki-read']['prefix'],logins['loki-read']['except']),('/loki','/loki/api/v1/push'))
        self.assertEqual(logins['loki-read']['from'],'monitoring/loki-read-auth')
        self.assertNotIn('monitoring-auth',logins['loki-read']['from']+logins['loki-push']['from'])
        self.assertIn({'name':'loki','prefix':'/loki','to':'monitoring/loki'},edge['root']['delegates'])

    def test_config_files_reach_configmaps_verbatim(self):
        docs=[d for d in yaml.safe_load_all(subprocess.check_output(['helm','template','loki',str(ROOT/'deploy/minikube/observability/loki'),'-n','monitoring'],text=True)) if d]
        maps={d['metadata']['name']:d['data'] for d in docs if d['kind']=='ConfigMap'}
        base=ROOT/'deploy/minikube/observability/loki'
        self.assertEqual(maps['loki-config']['loki.yaml'],(base/'loki.yaml').read_text())
        self.assertEqual(maps['alloy-config']['config.alloy'],(base/'config.alloy').read_text())
        # The PVCs outlive helm uninstall and terraform destroy.
        for claim in (d for d in docs if d['kind']=='PersistentVolumeClaim'):
            self.assertEqual(claim['metadata']['annotations']['helm.sh/resource-policy'],'keep')

    def test_retention_and_sanitization(self):
        cfg=yaml.safe_load((ROOT/'deploy/minikube/observability/loki/loki.yaml').read_text())
        self.assertEqual(cfg['limits_config']['retention_period'],'168h')
        self.assertTrue(cfg['compactor']['retention_enabled'])
        alloy=(ROOT/'deploy/minikube/observability/loki/config.alloy').read_text()
        self.assertIn('stage.output { source = "safe_line" }',alloy)
        # Experimental blocks crash Alloy at its default stability level.
        self.assertNotIn('wal {',alloy)
        self.assertNotIn('namespaces {',alloy)
        self.assertNotIn('target_label = "trace_id"',alloy)

    def test_datasource_preserves_prometheus(self):
        cfg=yaml.safe_load((ROOT/'deploy/minikube/observability/grafana/values.yaml').read_text())
        ds=cfg['datasources']['datasources.yaml']['datasources']
        self.assertEqual({d['type'] for d in ds},{'prometheus','loki','jaeger'})
        self.assertEqual([d['type'] for d in ds if d['isDefault']],['prometheus'])
        # Jaeger 2.x has only /api/v3; Grafana's Jaeger data source uses it only with this toggle.
        self.assertIn('jaegerEnableGrpcEndpoint',cfg['grafana.ini']['feature_toggles']['enable'])
        loki=next(d for d in ds if d['type']=='loki')
        self.assertIn('jaeger',[f.get('datasourceUid') for f in loki['jsonData']['derivedFields']])

if __name__=='__main__':unittest.main()
