"""mk4's public edge (deploy/minikube/istio/edge): routing order, logins that fail closed, client addresses, Traefik."""
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
    https, db, http = gateway['servers']
    assert https['tls']['credentialName'] == db['tls']['credentialName'] == VALUES['gateway']['certificate']['secretName']
    assert db['port']['protocol'] == 'TLS' and db['hosts'] == ['jithendar/db.jithendar.com']
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
    assert rules and all(r['when'] == [{'key': 'connection.sni', 'notValues': ['db.jithendar.com']}] for r in rules)
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


@pytest.mark.parametrize('traefik', [True, False])
def test_traefik_hand_off_and_the_proxy_header_follow_traefik(traefik):
    docs = render('--set', f'traefikHandoff.enabled={str(traefik).lower()}',
                  '--set', f'proxyProtocol.requireHeader={str(not traefik).lower()}')
    assert bool([d for d in docs if d['apiVersion'].startswith('traefik.io/')]) == traefik
    allow = {p['patch']['value']['typed_config']['allow_requests_without_proxy_protocol']
             for p in of(docs, 'EnvoyFilter', 'proxy-protocol')['spec']['configPatches']}
    assert allow == {traefik}


def test_terraform_ties_both_to_traefik_enabled():
    istio_tf = (ROOT / 'minikube-infrastructure/istio.tf').read_text()
    assert 'traefikHandoff = { enabled = var.traefik_enabled }' in istio_tf
    assert 'proxyProtocol  = { requireHeader = !var.traefik_enabled }' in istio_tf
