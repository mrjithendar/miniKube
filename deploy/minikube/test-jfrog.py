import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'jfrog'/f'{name}.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

bootstrap, preflight = load('bootstrap'), load('preflight')

class JFrogTests(unittest.TestCase):
    def test_small_shared_nodes_do_not_count_as_large_node(self):
        small = {'status': {'allocatable': {'cpu': '4', 'memory': '8Gi'}}}
        with self.assertRaises(RuntimeError): preflight.capacity({'items': [small]*3})
        preflight.capacity({'items': [small]}, 'minimal')
        preflight.capacity({'items': [{'status': {'allocatable': {'cpu': '8', 'memory': '16Gi'}}}]})

    def test_existing_credentials_never_rotate(self):
        doc = json.dumps({'data': {k:'encoded' for k in bootstrap.KEYS}})
        with patch.object(bootstrap.subprocess,'check_output',return_value=doc), patch.object(bootstrap.subprocess,'run') as write:
            bootstrap.ensure('mk4'); write.assert_not_called()

    def test_missing_credentials_with_data_fails(self):
        with patch.object(bootstrap.subprocess,'check_output',side_effect=['',json.dumps({'items':[{'metadata':{'name':'data'}}]})]), patch.object(bootstrap.subprocess,'run') as write:
            with self.assertRaises(RuntimeError): bootstrap.ensure('mk4')
            write.assert_not_called()

    def test_bootstrap_generates_independent_secrets_over_stdin(self):
        with patch.object(bootstrap.subprocess,'check_output',side_effect=['','{"items":[]}']), patch.object(bootstrap.subprocess,'run') as write:
            bootstrap.ensure('mk4')
            payload=json.loads(write.call_args.kwargs['input'])
            data=payload['stringData']
            self.assertEqual(set(data),set(bootstrap.KEYS))
            self.assertEqual(data['bootstrap.creds'],'admin@127.0.0.1='+data['admin-password'])
            self.assertNotEqual(data['password'],data['postgres-password'])
            self.assertEqual(len(data['master-key']),64)
            self.assertNotIn(data['admin-password'],' '.join(write.call_args.args[0]))

    def test_routes_do_not_take_over_existing_website(self):
        docs=[yaml.safe_load((ROOT/'jfrog'/name).read_text()) for name in yaml.safe_load((ROOT/'jfrog/kustomization.yaml').read_text())['resources']]
        for doc in docs:
            if doc['kind']=='Ingress':
                for rule in doc['spec']['rules']:
                    if rule['host']=='jithendar.com':
                        self.assertEqual([p['path'] for p in rule['http']['paths']],['/jfrog'])
        redirect=next(d for d in docs if d['metadata']['name']=='jfrog-shortcut' and d['kind']=='Middleware')
        self.assertEqual(redirect['spec']['redirectRegex']['replacement'],'https://jfrog.jithendar.com/ui/')

    def test_chart_database_secret_matches_bootstrap(self):
        values=yaml.safe_load((ROOT/'jfrog/values.yaml').read_text())['artifactory']
        self.assertEqual(values['database']['secrets']['password'],{'name':'jfrog-bootstrap','key':'password'})
        self.assertEqual(values['postgresql']['auth']['existingSecret'],'jfrog-bootstrap')
        self.assertEqual(values['nginx']['service']['type'],'ClusterIP')

if __name__=='__main__': unittest.main()
