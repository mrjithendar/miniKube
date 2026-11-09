"""mk4's shared Valkey (deploy/minikube/redis) and its access chart: HA with Sentinel and a master proxy, pods spread
over nodes on pinned volumes, STRICT mTLS with an enforced allow policy, the allowlist fails closed, routes point at
the master proxy, the edge routes /redis behind a login and terminates redis.jithendar.com."""
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / 'deploy/minikube/redis/access'
VALUES = yaml.safe_load((ROOT / 'deploy/minikube/redis/values.yaml').read_text())
EDGE = yaml.safe_load((ROOT / 'deploy/minikube/istio/edge/values.yaml').read_text())
PROMETHEUS = yaml.safe_load((ROOT / 'deploy/minikube/observability/prometheus/values.yaml').read_text())
POSTRENDER = ROOT / 'deploy/minikube/redis/postrender-haproxy.sh'
IPS = ['192.0.2.1/32', '198.51.100.0/24']
CLIENTS = ['harbor', 'jithendar']


def render(*args):
    out = subprocess.check_output(['helm', 'template', 'redis-access', str(ACCESS), '-n', 'redis', *args],
                                  text=True, stderr=subprocess.PIPE)
    return [d for d in yaml.safe_load_all(out) if d]


def of(docs, kind, name):
    return next(d for d in docs if d['kind'] == kind and d['metadata']['name'] == name)


def allowed():
    args = [a for i, ip in enumerate(IPS) for a in ('--set', f'allowedIps[{i}]={ip}')]
    return render(*args, '--set', 'clientNamespaces={' + ','.join(CLIENTS) + '}')


def test_sentinel_fails_over_and_the_master_proxy_hides_it():
    assert VALUES['architecture'] == 'replication' and VALUES['replicaCount'] == 3
    s = VALUES['sentinel']
    assert s['enabled'] and s['quorum'] == 2 and s['masterProxy']['enabled']
    assert s['valkeyShutdownWaitFailover'] and s['preStop']['enabled']
    assert VALUES['pdb'] == {'enabled': True, 'minAvailable': 2}   # one pod down at most: quorum survives a drain


def test_pods_spread_over_nodes_on_pinned_volumes():
    term = VALUES['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
    assert term['topologyKey'] == 'kubernetes.io/hostname'
    assert VALUES['persistence']['storageClass'] == 'local-path'
    assert VALUES['persistentVolumeClaimRetentionPolicy'] == {'enabled': True, 'whenDeleted': 'Retain', 'whenScaled': 'Retain'}


def test_queues_are_never_evicted_and_writes_are_durable():
    assert VALUES['config']['maxMemoryPolicy'] == 'volatile-lru'
    extra = VALUES['config']['extraConfig']
    assert 'appendonly yes' in extra and 'appendfsync everysec' in extra and 'databases 32' in extra


def test_tenants_are_terraform_variables_not_chart_values():
    # redis.tf appends the users to extraConfig; a user line here would be duplicated or overwritten.
    assert not any(line.startswith('user ') for line in VALUES['config']['extraConfig'])
    tfvars = (ROOT / 'minikube-infrastructure/vars/mk4.tfvars').read_text()
    for name, namespace in {'jithendar': 'jithendar', 'practice': 'jithendar', 'harbor': 'harbor', 'roboshop': 'roboshop-ai'}.items():
        assert f'{name}' in tfvars and f'namespace = "{namespace}"' in tfvars


def test_postrender_adds_a_tunnel_timeout_and_fails_when_the_chart_changes():
    rendered = '              defaults\n                  timeout client 30s\n                  timeout server 30s\n'
    out = subprocess.run([str(POSTRENDER)], input=rendered, capture_output=True, text=True, check=True).stdout
    assert out.splitlines()[-1] == '                  timeout tunnel 1h'
    failed = subprocess.run([str(POSTRENDER)], input='kind: Service\n', capture_output=True, text=True)
    assert failed.returncode != 0 and 'not found' in failed.stderr


def test_no_allowlist_no_render():
    with pytest.raises(subprocess.CalledProcessError) as failure:
        render()
    assert 'allowedIps is empty' in failure.value.stderr


def test_namespace_is_strict_and_only_known_callers_get_in():
    docs = allowed()
    assert of(docs, 'PeerAuthentication', 'default')['spec'] == {'mtls': {'mode': 'STRICT'}}
    valkey = of(docs, 'AuthorizationPolicy', 'valkey')
    assert valkey['spec']['action'] == 'ALLOW' and 'istio.io/dry-run' not in valkey['metadata'].get('annotations', {})
    data, sentinel, metrics = valkey['spec']['rules']
    assert data['from'][0]['source']['namespaces'] == ['redis', 'istio-ingress', *CLIENTS]
    assert data['to'][0]['operation']['ports'] == ['6379', '6380']
    assert sentinel['from'][0]['source']['namespaces'] == ['redis', *CLIENTS]
    assert metrics == {'from': [{'source': {'namespaces': ['monitoring']}}], 'to': [{'operation': {'ports': ['9121']}}]}
    ui = of(docs, 'AuthorizationPolicy', 'redis-ui')['spec']['rules']
    assert ui == [{'from': [{'source': {'namespaces': ['istio-ingress']}}], 'to': [{'operation': {'ports': ['5540']}}]}]


def test_allowlists_deny_everyone_else_on_the_gateway():
    docs = allowed()
    ui = of(docs, 'AuthorizationPolicy', 'redis-ui-allowlist')
    tcp = of(docs, 'AuthorizationPolicy', 'redis-tcp-allowlist')
    for policy in (ui, tcp):
        assert policy['metadata']['namespace'] == 'istio-ingress' and policy['spec']['action'] == 'DENY'
        assert policy['spec']['rules'][0]['from'][0]['source']['notRemoteIpBlocks'] == IPS
    assert ui['spec']['rules'][0]['to'][0]['operation'] == {'ports': ['443'], 'paths': ['/redis', '/redis/*']}
    assert ui['spec']['rules'][0]['when'] == [{'key': 'connection.sni', 'notValues': ['redis.jithendar.com']}]
    assert tcp['spec']['rules'][0]['when'] == [{'key': 'connection.sni', 'values': ['redis.jithendar.com']}]


def test_tcp_route_and_console_go_to_the_master_proxy():
    docs = allowed()
    tcp = of(docs, 'VirtualService', 'redis-tcp')['spec']
    assert tcp['hosts'] == ['redis.jithendar.com'] and tcp['gateways'] == ['istio-ingress/public']
    assert tcp['tcp'][0]['route'][0]['destination'] == {
        'host': 'valkey-master.redis.svc.cluster.local', 'port': {'number': 6379}}
    ui = of(docs, 'VirtualService', 'redis-ui')['spec']
    assert 'hosts' not in ui and 'gateways' not in ui
    env = {e['name']: e for e in of(docs, 'Deployment', 'redis-ui')['spec']['template']['spec']['containers'][0]['env']}
    assert env['RI_PROXY_PATH']['value'] == '/redis'
    assert env['RI_REDIS_HOST']['value'] == 'valkey-master.redis.svc.cluster.local'
    assert env['RI_REDIS_USERNAME']['value'] == 'console'
    assert env['RI_REDIS_PASSWORD']['valueFrom']['secretKeyRef'] == {'name': 'valkey-users', 'key': 'console'}


def test_metrics_service_declares_its_port_for_mtls():
    svc = of(allowed(), 'Service', 'valkey-metrics-pods')['spec']
    assert svc['clusterIP'] == 'None' and svc['ports'][0]['port'] == 9121
    job = next(j for j in PROMETHEUS['extraScrapeConfigs'] if j['job_name'] == 'redis')
    assert job['dns_sd_configs'][0] == {'names': ['valkey-metrics-pods.redis.svc.cluster.local'], 'type': 'A', 'port': 9121}


def test_edge_routes_redis_behind_a_login_and_terminates_its_host():
    assert {'name': 'redis', 'prefix': '/redis', 'to': 'redis/redis-ui'} in EDGE['root']['delegates']
    assert {'name': 'redis', 'prefix': '/redis', 'from': 'redis/redis-ui-auth'} in EDGE['basicAuth']
    assert 'redis/redis.jithendar.com' in [t['host'] for t in EDGE['gateway']['tls']]
