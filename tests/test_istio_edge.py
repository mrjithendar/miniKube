"""mk4's public edge (deploy/minikube/istio/edge) and mesh policies (istio/mesh): routing order, logins that fail
closed, client addresses, response headers, mTLS, who may call whom."""
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / 'deploy/minikube/istio/edge'
VALUES = yaml.safe_load((CHART / 'values.yaml').read_text())
LOGINS = [login['name'] for login in VALUES['basicAuth']]
USERS = {name: f'{name}:{{SHA}}' + 'A' * 27 + '=' for name in LOGINS}


def render(*args, users=USERS):
    command = ['helm', 'template', 'edge', str(CHART), '-n', 'istio-ingress', *args]
    if users is not None:
        command += ['--set-json', 'basicAuthUsers=' + json.dumps(users)]
    return [d for d in yaml.safe_load_all(subprocess.check_output(command, text=True, stderr=subprocess.PIPE)) if d]


def of(docs, kind, name):
    return next(d for d in docs if d['kind'] == kind and d['metadata']['name'] == name)


def test_a_longer_prefix_comes_before_the_one_it_starts_with_and_the_site_is_last():
    routes = of(render(), 'VirtualService', 'jithendar-com')['spec']['http']
    prefixes = [r['match'][0]['uri']['prefix'] for r in routes if r.get('match') and 'uri' in r['match'][0]]
    for i, earlier in enumerate(prefixes):
        assert not any(later.startswith(earlier) and later != earlier for later in prefixes[i + 1:]), earlier
    assert routes[0]['redirect'] == {'authority': 'jithendar.com', 'redirectCode': 308}   # www first
    assert routes[-1] == {'name': 'site', 'delegate': {'name': 'jithendar-com-site', 'namespace': 'jithendar'}}
    assert all('route' not in r for r in routes)   # the root only redirects and delegates


def test_only_the_owning_namespace_binds_a_hostname():
    gateway = of(render(), 'Gateway', 'public')['spec']
    hosts = [h for s in gateway['servers'] for h in s['hosts']]
    assert all('/' in h and not h.startswith('*/') for h in hosts)
    https, db, postgres, http = gateway['servers']
    for tcp in (db, postgres):
        assert tcp['tls']['credentialName'] == https['tls']['credentialName'] == VALUES['gateway']['certificate']['secretName']
        assert tcp['port']['protocol'] == 'TLS'
    assert db['hosts'] == ['jithendar/db.jithendar.com']
    assert postgres['hosts'] == ['postgres-db/postgres.jithendar.com']
    assert http['tls'] == {'httpsRedirect': True}   # plain HTTP never reaches a login


def test_every_except_is_covered_by_another_login():
    for login in VALUES['basicAuth']:
        if 'except' in login:
            assert any(other is not login and login['except'].startswith(other['prefix']) for other in VALUES['basicAuth']), login


def test_logins_are_required_to_render():
    with pytest.raises(subprocess.CalledProcessError) as failure:
        render(users={})
    assert 'gateway-basic-auth.py' in failure.value.stderr


def test_logins_run_on_https_only_and_skip_paths_outside_their_prefix():
    patches = of(render(), 'EnvoyFilter', 'basic-auth')['spec']['configPatches']
    assert len(patches) == len(LOGINS)
    for patch, login in zip(patches, VALUES['basicAuth']):
        assert patch['match']['listener']['portNumber'] == 443
        value = patch['patch']['value']['typed_config']
        assert value['extension_config']['typed_config']['users']['inline_string'] == USERS[login['name']]
        matchers = value['xds_matcher']['matcher_list']['matchers']
        # Last matcher: skip unless the path starts with the prefix. No on_no_match: everything else must log in.
        assert matchers[-1]['predicate']['not_matcher']['single_predicate']['value_match'] == {'prefix': login['prefix']}
        assert 'on_no_match' not in value['xds_matcher']
        if 'except' in login:
            assert matchers[0]['predicate']['single_predicate']['value_match'] == {'prefix': login['except']}


def test_read_only_rules_never_touch_the_tcp_hosts():
    rules = of(render(), 'AuthorizationPolicy', 'read-only')['spec']['rules']
    assert rules and all(r['when'] == [{'key': 'connection.sni', 'notValues': ['db.jithendar.com', 'postgres.jithendar.com']}] for r in rules)
    assert all(r['to'][0]['operation']['ports'] == ['443'] for r in rules)


def test_clients_cannot_choose_their_address():
    docs = render()
    headers = of(docs, 'EnvoyFilter', 'forwarded-for')['spec']['configPatches'][0]['patch']['value']['request_headers_to_add']
    assert {h['header']['key']: h['append_action'] for h in headers} == {'x-forwarded-for': 'OVERWRITE_IF_EXISTS_OR_ADD',
                                                                         'x-real-ip': 'OVERWRITE_IF_EXISTS_OR_ADD'}
    first = of(docs, 'EnvoyFilter', 'proxy-protocol')['spec']['configPatches']
    assert {p['match']['listener']['portNumber'] for p in first} == {80, 443}
    assert all(p['patch']['operation'] == 'INSERT_FIRST' for p in first)
    gateway_values = yaml.safe_load((ROOT / 'deploy/minikube/istio/ingressgateway.yaml').read_text())
    assert 'numTrustedProxies' not in json.dumps(gateway_values)


def test_the_proxy_header_is_required_and_nothing_targets_traefik():
    docs = render()
    assert not [d for d in docs if d['apiVersion'].startswith('traefik.io/')]
    allow = {p['patch']['value']['typed_config']['allow_requests_without_proxy_protocol']
             for p in of(docs, 'EnvoyFilter', 'proxy-protocol')['spec']['configPatches']}
    assert allow == {False}
    assert 'traefik' not in (ROOT / 'minikube-infrastructure/istio.tf').read_text()


def test_visitors_never_see_backend_timings():
    patch = of(render(), 'EnvoyFilter', 'response-headers')['spec']['configPatches'][0]
    assert patch['applyTo'] == 'ROUTE_CONFIGURATION' and patch['match']['context'] == 'GATEWAY'
    assert 'x-envoy-upstream-service-time' in patch['patch']['value']['response_headers_to_remove']


MESH = ROOT / 'deploy/minikube/istio/mesh'


def render_mesh(*args):
    command = ['helm', 'template', 'istio-mesh', str(MESH), '-n', 'istio-system', *args]
    return [d for d in yaml.safe_load_all(subprocess.check_output(command, text=True, stderr=subprocess.PIPE)) if d]


def test_mesh_default_is_strict_in_the_root_namespace():
    pa = of(render_mesh(), 'PeerAuthentication', 'default')
    assert pa['metadata']['namespace'] == 'istio-system' and 'selector' not in pa['spec']
    assert pa['spec']['mtls']['mode'] == 'STRICT'


def test_authorization_allows_own_namespace_and_starts_as_dry_run():
    policies = [d for d in render_mesh() if d['kind'] == 'AuthorizationPolicy']
    assert policies and all(p['metadata']['annotations'] == {'istio.io/dry-run': 'true'} for p in policies)
    for p in policies:
        namespace = p['metadata']['namespace']
        assert namespace != 'istio-system'
        assert 'selector' not in p['spec'] and p['spec']['action'] == 'ALLOW'
        assert p['spec']['rules'][0]['from'][0]['source']['namespaces'][0] == namespace
    enforced = [d for d in render_mesh('--set', 'authorization.dryRun=false') if d['kind'] == 'AuthorizationPolicy']
    assert all('annotations' not in p['metadata'] for p in enforced)


def test_every_listed_namespace_has_its_template():
    listed = set(yaml.safe_load((MESH / 'values.yaml').read_text())['authorization']['namespaces'])
    files = {p.stem.removeprefix('authorization-') for p in (MESH / 'templates').glob('authorization-*.yaml')} - {'guard'}
    assert listed == files


def test_authorization_never_lists_the_root_namespace():
    command = ['helm', 'template', 'm', str(MESH), '-n', 'istio-system', '--set-json',
               'authorization.namespaces={"istio-system": ["monitoring"]}']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and 'whole mesh' in result.stderr


def test_gateway_access_log_only_has_fields_alloy_keeps():
    telemetry = of(render_mesh(), 'Telemetry', 'edge-access-log')
    assert telemetry['spec']['selector']['matchLabels'] == {'istio': 'ingressgateway'}
    istiod = yaml.safe_load((ROOT / 'deploy/minikube/istio/istiod.yaml').read_text())
    provider = next(p for p in istiod['meshConfig']['extensionProviders'] if p['name'] == 'edge-access-log')
    labels = provider['envoyFileAccessLog']['logFormat']['labels']
    # Alloy's allowlist (observability/loki/config.alloy); never a client address, path, query or header.
    assert set(labels) <= {'event', 'level', 'method', 'status', 'durationMs', 'operation', 'errorCode', 'route',
                           'dependency', 'outcome', 'errorType', 'logger_name', 'traceId'}
    assert not any(word in json.dumps(labels) for word in ('PATH', 'REMOTE_ADDRESS', 'X-FORWARDED', 'AUTHORITY'))


def test_edge_and_control_plane_survive_one_node():
    gateway = yaml.safe_load((ROOT / 'deploy/minikube/istio/ingressgateway.yaml').read_text())
    assert gateway['replicaCount'] >= 2 and gateway['podDisruptionBudget'] == {'minAvailable': 1}
    istiod = yaml.safe_load((ROOT / 'deploy/minikube/istio/istiod.yaml').read_text())
    assert istiod['pilot']['replicaCount'] >= 2
    kiali = yaml.safe_load((ROOT / 'deploy/minikube/istio/kiali.yaml').read_text())
    assert kiali['deployment']['pod_labels'] == {'sidecar.istio.io/inject': 'true'}   # Prometheus is STRICT
