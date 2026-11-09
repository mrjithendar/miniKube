"""mk4's CloudNativePG cluster (deploy/minikube/postgres) and its public access: allowlist fails closed, routes point at
the primary, the edge routes /postgres and terminates postgres.jithendar.com, volumes are node-pinned."""
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / 'deploy/minikube/postgres/access'
CLUSTER = yaml.safe_load((ROOT / 'deploy/minikube/postgres/cluster.yaml').read_text())
EDGE = yaml.safe_load((ROOT / 'deploy/minikube/istio/edge/values.yaml').read_text())
STORAGE = yaml.safe_load((ROOT / 'deploy/minikube/storage/local-path.yaml').read_text())
IPS = ['192.0.2.1/32', '198.51.100.0/24']


def render(*args):
    out = subprocess.check_output(['helm', 'template', 'postgres-access', str(ACCESS), '-n', 'postgres-db', *args],
                                  text=True, stderr=subprocess.PIPE)
    return [d for d in yaml.safe_load_all(out) if d]


def of(docs, kind, name):
    return next(d for d in docs if d['kind'] == kind and d['metadata']['name'] == name)


def allowed():
    return render(*[a for i, ip in enumerate(IPS) for a in ('--set', f'allowedIps[{i}]={ip}')])


def test_no_allowlist_no_render():
    with pytest.raises(subprocess.CalledProcessError) as failure:
        render()
    assert 'allowedIps is empty' in failure.value.stderr


def test_allowlists_deny_everyone_else_on_the_gateway():
    docs = allowed()
    ui = of(docs, 'AuthorizationPolicy', 'postgres-ui-allowlist')
    tcp = of(docs, 'AuthorizationPolicy', 'postgres-tcp-allowlist')
    for policy in (ui, tcp):
        assert policy['metadata']['namespace'] == 'istio-ingress' and policy['spec']['action'] == 'DENY'
        assert policy['spec']['rules'][0]['from'][0]['source']['notRemoteIpBlocks'] == IPS
    assert ui['spec']['rules'][0]['to'][0]['operation'] == {'ports': ['443'], 'paths': ['/postgres', '/postgres/*']}
    assert ui['spec']['rules'][0]['when'] == [{'key': 'connection.sni', 'notValues': ['postgres.jithendar.com']}]
    assert tcp['spec']['rules'][0]['when'] == [{'key': 'connection.sni', 'values': ['postgres.jithendar.com']}]


def test_tcp_route_goes_to_the_primary_and_the_ui_is_a_delegate():
    docs = allowed()
    tcp = of(docs, 'VirtualService', 'postgres-tcp')['spec']
    assert tcp['hosts'] == ['postgres.jithendar.com'] and tcp['gateways'] == ['istio-ingress/public']
    assert tcp['tcp'][0]['route'][0]['destination'] == {
        'host': 'postgres-cluster-rw.postgres-db.svc.cluster.local', 'port': {'number': 5432}}
    ui = of(docs, 'VirtualService', 'postgres-ui')['spec']
    assert 'hosts' not in ui and 'gateways' not in ui


def test_edge_routes_and_terminates_the_postgres_host():
    assert {'name': 'postgres', 'prefix': '/postgres', 'to': 'postgres-db/postgres-ui'} in EDGE['root']['delegates']
    tls = {t['host']: t for t in EDGE['gateway']['tls']}
    assert tls['postgres-db/postgres.jithendar.com']['alpn'] == ['postgresql']


def test_instances_spread_over_nodes_on_pinned_volumes():
    c = CLUSTER['cluster']
    assert c['instances'] >= 2 and c['storage']['storageClass'] == STORAGE['storageClass']['name'] == 'local-path'
    assert c['affinity'] == {'topologyKey': 'kubernetes.io/hostname', 'enablePodAntiAffinity': True,
                             'podAntiAffinityType': 'required'}
    assert STORAGE['storageClass']['volumeBindingMode'] == 'WaitForFirstConsumer'
    assert STORAGE['storageClass']['reclaimPolicy'] == 'Retain' and not STORAGE['storageClass']['defaultClass']


def test_applications_go_through_session_mode_pgbouncer():
    poolers = CLUSTER['poolers']
    assert [p['name'] for p in poolers] == ['rw'] and poolers[0]['type'] == 'rw'
    # transaction mode breaks prepared statements, advisory locks and SET for the applications that move here
    assert poolers[0]['poolMode'] == 'session' and poolers[0]['instances'] >= 2


def test_tenants_are_terraform_variables_not_chart_values():
    # postgres.tf merges roles and databases from var.postgres_databases; values here would be overwritten.
    assert 'roles' not in CLUSTER['cluster'] and 'databases' not in CLUSTER
    tfvars = (ROOT / 'minikube-infrastructure/vars/mk4.tfvars').read_text()
    for name, namespace in {'jithendar': 'jithendar', 'moks': 'moks', 'registry': 'harbor', 'shipping': 'roboshop-ai'}.items():
        assert f'{name}' in tfvars and f'namespace = "{namespace}"' in tfvars


def test_ui_is_pgadmin_under_the_prefix_with_its_state_kept():
    docs = allowed()
    pod = of(docs, 'Deployment', 'postgres-ui')['spec']['template']['spec']
    c = pod['containers'][0]
    env = {e['name']: e.get('value', e.get('valueFrom')) for e in c['env']}
    assert c['image'].startswith('dpage/pgadmin4:') and not c['image'].endswith(':latest')
    assert env['SCRIPT_NAME'] == '/postgres' and env['PGADMIN_LISTEN_PORT'] == '8080'
    # two gateway pods forward requests: a session tied to the client address would drop
    assert env['PGADMIN_CONFIG_ENHANCED_COOKIE_PROTECTION'] == 'False'
    assert env['PGADMIN_DEFAULT_PASSWORD'] == {'secretKeyRef': {'name': 'postgres-ui-admin', 'key': 'password'}}
    assert c['readinessProbe']['httpGet']['path'] == '/postgres/misc/ping'
    pvc = of(docs, 'PersistentVolumeClaim', 'postgres-ui-data')
    assert pvc['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'
    assert {'name': 'data', 'persistentVolumeClaim': {'claimName': 'postgres-ui-data'}} in pod['volumes']
    route = of(docs, 'VirtualService', 'postgres-ui')['spec']['http'][1]
    assert route['headers']['response']['set']['X-Frame-Options'] == 'SAMEORIGIN'
