"""Harbor on mk4 (deploy/minikube/harbor): offline contracts. Rendering the pinned chart is validate.py --external-charts."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HARBOR = ROOT / 'deploy/minikube/harbor'
OPTIONAL = ROOT / 'deploy/minikube/applications/optional'
VALUES = yaml.safe_load((HARBOR / 'values.yaml').read_text())
APP = yaml.safe_load((OPTIONAL / 'harbor.yaml').read_text())
SCRIPT = (HARBOR / 'deploy-harbor.sh').read_text()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


credentials = load('harbor_credentials', HARBOR / 'credentials.py')
deploy = load('harbor_deploy', ROOT / 'scripts/argocd/deploy.py')
validate = load('harbor_validate', ROOT / 'scripts/argocd/validate.py')


def manifests():
    return [d for d in yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(HARBOR / 'manifests')], text=True)) if d]


def by_name(kind):
    return {d['metadata']['name']: d for d in manifests() if d['kind'] == kind}


def pod_specs():
    """Pod specs Harbor runs outside the chart, with where they come from."""
    for doc in manifests() + [yaml.safe_load((HARBOR / 'examples/harbor-restore-job.yaml').read_text())]:
        spec = doc['spec']
        if doc['kind'] == 'CronJob':
            spec = spec['jobTemplate']['spec']
        if 'template' in spec:
            yield doc['metadata']['name'], spec['template']['spec']


def test_harbor_is_opt_in(capsys):
    assert 'harbor.yaml' not in yaml.safe_load((ROOT / 'deploy/minikube/applications/kustomization.yaml').read_text())['resources']
    assert 'harbor' not in deploy.catalog()[0]
    with patch('sys.argv', ['deploy', '--dry-run']), patch.object(deploy.subprocess, 'check_output') as run:
        deploy.main()
    run.assert_not_called()
    assert 'harbor' not in capsys.readouterr().out
    with patch('sys.argv', ['deploy', 'harbor', '--dry-run']), patch.object(deploy.subprocess, 'check_output') as run:
        deploy.main()
    run.assert_not_called()
    assert 'action: harbor' in capsys.readouterr().out
    with patch('sys.argv', ['deploy', 'list']):
        deploy.main()
    assert 'Opt-in apps (by name only, never in all): harbor' in capsys.readouterr().out


def test_selecting_harbor_creates_its_project_first():
    # Fresh cluster: CRDs present, no Applications, no AppProject mk-registry.
    healthy = {'items': [{**APP, 'status': {'operationState': {'phase': 'Succeeded'}, 'sync': {'status': 'Synced'},
                                             'health': {'status': 'Healthy'}, 'resources': [{'kind': 'Deployment'}]}}]}
    outputs = ['crd/applications', 'crd/appprojects', json.dumps({'items': []}), '', 'appproject created\n',
               'application created\n', 'patched\n', json.dumps(healthy)]
    with patch('sys.argv', ['deploy', 'harbor']), patch.object(deploy.subprocess, 'check_output', side_effect=outputs) as run, \
            patch.object(deploy.subprocess, 'check_call') as apply:
        deploy.main()
    assert apply.call_args.args[0][-1].endswith('deploy/minikube/namespaces/harbor.yaml')
    created = [json.loads(c.kwargs['input']) for c in run.call_args_list if 'create' in c.args[0]]
    assert [d['kind'] for d in created] == ['AppProject', 'Application']
    assert created[0]['metadata']['name'] == APP['spec']['project'] == 'mk-registry'
    sync = json.loads(run.call_args_list[6].kwargs['input'])['operation']['sync']
    assert sync['prune'] is False


def test_application_pins_the_official_chart_and_reads_tools():
    chart, values, path = APP['spec']['sources']
    assert (chart['repoURL'], chart['chart'], chart['targetRevision']) == ('https://helm.goharbor.io', 'harbor', '1.19.4')
    assert chart['helm'] == {'releaseName': 'harbor', 'valueFiles': ['$values/deploy/minikube/harbor/values.yaml']}
    assert values == {'repoURL': 'https://github.com/mrjithendar/miniKube.git', 'targetRevision': 'master', 'ref': 'values'}
    assert path['path'] == 'deploy/minikube/harbor/manifests'
    assert APP['spec']['destination'] == {'server': 'https://kubernetes.default.svc', 'namespace': 'harbor'}
    assert 'automated' not in APP['spec']['syncPolicy']
    assert APP['metadata']['annotations']['argocd.argoproj.io/sync-options'] == 'Prune=false,Delete=false'


def test_project_allows_only_the_harbor_namespace():
    project = yaml.safe_load((OPTIONAL / 'project-registry.yaml').read_text())['spec']
    assert project['destinations'] == [{'namespace': 'harbor', 'server': 'https://kubernetes.default.svc'}]
    assert project['clusterResourceWhitelist'] == [{'group': '', 'kind': 'Namespace'}]
    assert set(project['sourceRepos']) == {'https://github.com/mrjithendar/miniKube.git', 'https://helm.goharbor.io'}
    assert {'group': '*', 'kind': '*'} not in project['namespaceResourceWhitelist']


def test_namespace_manifest_matches_deploy_contract():
    assert yaml.safe_load((ROOT / 'deploy/minikube/namespaces/harbor.yaml').read_text()) == \
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'harbor', 'labels': {'istio-injection': 'enabled'}}}


EDGE = yaml.safe_load((ROOT / 'deploy/minikube/istio/edge/values.yaml').read_text())


def test_one_hostname_everywhere():
    host = VALUES['externalURL'].removeprefix('https://')
    assert host == 'registry.jithendar.com'
    assert by_name('Certificate')['harbor-public']['spec']['dnsNames'] == [host]
    route = by_name('VirtualService')['harbor']['spec']
    assert route['hosts'] == [host] and route['gateways'] == ['istio-ingress/public']
    # The Gateway lets only namespace harbor bind the hostname, and jithendar.com/harbor redirects there.
    assert f'harbor/{host}' in EDGE['gateway']['https']
    assert {'name': 'harbor', 'prefix': '/harbor', 'authority': host} in EDGE['root']['redirects']


def test_gateway_routes_what_the_chart_ingress_did():
    # No Ingress from the chart: clusterIP, and its nginx (no arm64 build) never runs.
    assert VALUES['expose']['type'] == 'clusterIP' and VALUES['nginx']['replicas'] == 0
    assert not by_name('Ingress') and not by_name('Middleware')
    core, portal = by_name('VirtualService')['harbor']['spec']['http']
    assert [m['uri']['prefix'] for m in core['match']] == ['/api/', '/service/', '/v2/', '/c/']
    assert core['route'][0]['destination']['host'] == 'harbor-core.harbor.svc.cluster.local'
    assert 'match' not in portal and portal['route'][0]['destination']['host'] == 'harbor-portal.harbor.svc.cluster.local'
    assert 'timeout' not in core   # a large layer push takes minutes
    assert VALUES['expose']['tls'] == {'enabled': True, 'certSource': 'secret', 'secret': {'secretName': 'harbor-public-tls'}}
    assert VALUES['caSecretName'] == 'harbor-public-tls'   # else core's ca-download volume renders with no source
    certificate = by_name('Certificate')['harbor-public']['spec']
    assert certificate['secretName'] == 'harbor-public-tls' and certificate['issuerRef']['name'] == 'letsencrypt-dns'


def test_everything_runs_on_one_node():
    nodes = {VALUES[c]['nodeSelector']['kubernetes.io/hostname'] for c in ('portal', 'core', 'jobservice', 'registry', 'trivy')}
    nodes |= {spec['nodeSelector']['kubernetes.io/hostname'] for _, spec in pod_specs()}
    assert nodes == {'mk4-m05'}


def test_chart_images_are_the_tags_the_script_pushes():
    version = re.search(r'^HARBOR_VERSION=(\S+)', SCRIPT, re.M).group(1)
    repo = re.search(r'^IMAGE_REPO="\$\{IMAGE_REPO:-([^}]+)\}"', SCRIPT, re.M).group(1)
    pushed = set(re.search(r'^IMAGES="([^"]+)"', SCRIPT, re.M).group(1).split()) | {'trivy-adapter-photon'}
    images = [VALUES[c]['image'] for c in ('portal', 'core', 'jobservice', 'trivy')]
    images += [VALUES['registry'][c]['image'] for c in ('registry', 'controller')]
    for image in images:
        assert image['repository'] == f'docker.io/{repo}', image
        component, _, tag_version = image['tag'].rpartition('-')
        assert component in pushed and tag_version == version, image
    assert repo == 'jithendar/custom-harbour'
    assert '"$IMAGE_REPO:$image-$HARBOR_VERSION"' in SCRIPT
    assert VALUES['imagePullSecrets'] == [{'name': re.search(r'^PULL_SECRET=(\S+)', SCRIPT, re.M).group(1)}]
    assert VALUES['metrics']['enabled'] is False and VALUES['trivy']['enabled'] is False


def test_script_pins_source_refuses_the_cluster_host_and_deploys_from_master():
    assert re.search(r'^HARBOR_COMMIT=[0-9a-f]{40}\b', SCRIPT, re.M)
    assert '[ "$(git -C "$WORK/harbor" rev-parse HEAD)" = "$HARBOR_COMMIT" ]' in SCRIPT
    assert "grep -qx mk4" in SCRIPT and "'{{.Server.Arch}}')\" = arm64" in SCRIPT
    assert 'PULL_BASE_FROM_DOCKERHUB=false' in SCRIPT and 'make/photon/common/Dockerfile' in SCRIPT
    # Argo CD renders from master, so the deploy refuses a checkout that differs from it.
    assert 'git -C "$ROOT" diff --quiet origin/master -- deploy/minikube/harbor' in SCRIPT
    assert '--services harbor --apply' in SCRIPT and 'scripts/argocd/deploy.py" harbor' in SCRIPT
    # The Docker Hub token goes to kubectl on stdin, never argv.
    assert 'DOCKERHUB_TOKEN="$token" python3 -c' in SCRIPT and '--docker-password' not in SCRIPT
    subprocess.run(['bash', '-n', str(HARBOR / 'deploy-harbor.sh')], check=True)
    assert not (HARBOR / 'build-images.sh').exists()


def test_build_workflow_is_manual_arm64_and_only_builds():
    text = (ROOT / '.github/workflows/build-harbor-images.yml').read_text()
    workflow = yaml.safe_load(text)
    assert list(workflow[True]) == ['workflow_dispatch']   # PyYAML reads the key "on" as True
    job = workflow['jobs']['build']
    assert job['runs-on'] == 'ubuntu-24.04-arm' and workflow['permissions'] == {'contents': 'read'}
    assert 'args=(--skip-deploy)' in text and 'deploy-harbor.sh "${args[@]}"' in text
    assert '--password-stdin' in text and '--password "' not in text


def test_script_rejects_unknown_arguments_without_side_effects():
    result = subprocess.run(['bash', str(HARBOR / 'deploy-harbor.sh'), '--bogus'], capture_output=True, text=True)
    assert result.returncode == 1 and 'unknown argument' in result.stderr


def references_to(secret):
    """Keys of `secret` that values.yaml and the manifests read."""
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get('secretKeyRef')
            if ref and ref['name'] == secret:
                keys.add(ref['key'])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(VALUES)
    for _, spec in pod_specs():
        walk(spec)
    return keys


def test_every_chart_credential_is_external_and_provisioned():
    secret = credentials.NAME
    assert VALUES['existingSecretAdminPassword'] == VALUES['existingSecretSecretKey'] == secret
    assert VALUES['core']['existingSecret'] == VALUES['core']['existingXsrfSecret'] == secret
    assert VALUES['jobservice']['existingSecret'] == VALUES['registry']['existingSecret'] == secret
    assert VALUES['registry']['credentials']['existingSecret'] == VALUES['database']['external']['existingSecret'] == secret
    assert VALUES['core']['secretName'] == by_name('Certificate')['harbor-token']['spec']['secretName']
    assert VALUES['registry']['credentials']['username'] == credentials.REGISTRY_USER
    # Chart-defined key names (values.yaml comments) plus every secretKeyRef.
    chart_keys = {VALUES['existingSecretAdminPasswordKey'], 'secretKey', 'secret', VALUES['core']['existingXsrfSecretKey'],
                  VALUES['jobservice']['existingSecretKey'], VALUES['registry']['existingSecretKey'],
                  'REGISTRY_PASSWD', 'REGISTRY_HTPASSWD', 'password'}
    assert chart_keys | references_to(secret) == set(credentials.KEYS)
    # Never set in Git.
    assert 'harborAdminPassword' not in VALUES and 'secretKey' not in VALUES
    assert VALUES['redis']['external']['password'] == '' and 'existingSecret' not in VALUES['redis']['external']
    assert VALUES['database']['type'] == VALUES['redis']['type'] == 'external'


@pytest.mark.parametrize('component,names', [
    ('core', ['_REDIS_URL_CORE', '_REDIS_URL_REG']),
    ('jobservice', ['_REDIS_URL_CORE', 'JOB_SERVICE_POOL_REDIS_URL']),
])
def test_redis_urls_carry_the_password_at_runtime(component, names):
    env = VALUES[component]['extraEnvVars']
    order = [e['name'] for e in env]
    assert order[0] == 'REDIS_PASSWORD'   # $(VAR) only expands variables defined earlier in the list
    addr = VALUES['redis']['external']['addr']
    for name in names:
        value = next(e['value'] for e in env if e['name'] == name)
        assert value.startswith(f'redis://:$(REDIS_PASSWORD)@{addr}/'), value
    indexes = {'_REDIS_URL_CORE': 'coreDatabaseIndex', '_REDIS_URL_REG': 'registryDatabaseIndex', 'JOB_SERVICE_POOL_REDIS_URL': 'jobserviceDatabaseIndex'}
    for name in names:
        value = next(e['value'] for e in env if e['name'] == name)
        assert value.split('/')[3].split('?')[0] == VALUES['redis']['external'][indexes[name]]


def test_registry_containers_get_the_redis_password():
    for container in ('registry', 'controller'):
        env = VALUES['registry'][container]['extraEnvVars']
        assert env == [{'name': 'REGISTRY_REDIS_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'harbor-credentials', 'key': 'REDIS_PASSWORD'}}}]


def test_database_and_redis_volumes_survive_prune_and_sync_first():
    for name in ('harbor-postgres-data', 'harbor-redis-data'):
        annotations = by_name('PersistentVolumeClaim')[name]['metadata']['annotations']
        assert annotations['argocd.argoproj.io/sync-options'] == 'Prune=false,Delete=false'
    for name in ('harbor-postgres', 'harbor-redis'):
        assert by_name('StatefulSet')[name]['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '-1'
    claim = VALUES['persistence']['persistentVolumeClaim']['registry']
    assert claim['annotations']['argocd.argoproj.io/sync-options'] == 'Prune=false,Delete=false'
    assert VALUES['persistence']['resourcePolicy'] == 'keep'


def test_nothing_waits_on_a_later_sync_wave():
    # Argo CD waits for each wave to be healthy before the next: a wave -1 Certificate whose Issuer sat in wave 0
    # never became Ready, and the first sync hung.
    def wave(doc):
        return int(doc['metadata'].get('annotations', {}).get('argocd.argoproj.io/sync-wave', '0'))
    for certificate in by_name('Certificate').values():
        ref = certificate['spec']['issuerRef']
        if ref.get('kind', 'Issuer') == 'Issuer':
            assert wave(by_name('Issuer')[ref['name']]) < wave(certificate), certificate['metadata']['name']
    for workload in by_name('StatefulSet').values():
        for volume in workload['spec']['template']['spec']['volumes']:
            if 'persistentVolumeClaim' in volume:
                claim = by_name('PersistentVolumeClaim')[volume['persistentVolumeClaim']['claimName']]
                assert wave(claim) <= wave(workload), volume


def test_redis_password_never_reaches_a_command_line():
    container = by_name('StatefulSet')['harbor-redis']['spec']['template']['spec']['containers'][0]
    script = container['args'][0]
    assert 'requirepass' in script and '> /run/valkey/valkey.conf' in script and 'unset REDIS_PASSWORD' in script
    assert not re.search(r'valkey-server[^\n]*\$REDIS_PASSWORD', script)
    assert {'name': 'REDISCLI_AUTH', 'valueFrom': {'secretKeyRef': {'name': 'harbor-credentials', 'key': 'REDIS_PASSWORD'}}} in container['env']
    for probe in ('readinessProbe', 'livenessProbe'):
        assert ' -a ' not in ' '.join(container[probe]['exec']['command'])


def test_backup_is_suspended_and_dumps_before_copying():
    cron = by_name('CronJob')['harbor-backup']['spec']
    assert cron['suspend'] is True and cron['concurrencyPolicy'] == 'Forbid'
    pod = cron['jobTemplate']['spec']['template']['spec']
    assert [c['name'] for c in pod['initContainers']] == ['dump']
    upload = pod['containers'][0]['args'][0]
    assert 'rclone copy ' in upload and 'rclone sync' not in upload
    assert upload.index('postgres/registry-') < upload.index('rclone copy /storage') < upload.index('/LATEST')
    claim = next(v for v in pod['volumes'] if v['name'] == 'registry-data')['persistentVolumeClaim']
    assert claim == {'claimName': 'harbor-registry', 'readOnly': True}
    # harbor-credentials itself is never uploaded: only the database password is read, by pg_dump.
    assert {r['secretKeyRef']['key'] for c in pod['initContainers'] + pod['containers'] for e in c.get('env', [])
            for r in [e.get('valueFrom', {})] if r.get('secretKeyRef', {}).get('name') == 'harbor-credentials'} == {'password'}


def test_validator_rejects_a_harbor_secret_with_a_value(monkeypatch):
    original = validate.render

    def leaky(app, external=False):
        rendered = original(app, external)
        if app['metadata']['name'] == 'harbor':
            rendered.append({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'harbor-core'},
                             'data': {'HARBOR_ADMIN_PASSWORD': 'SGFyYm9yMTIzNDU='}})
        return rendered
    monkeypatch.setattr(validate, 'render', leaky)
    with pytest.raises(AssertionError, match='carries a value'):
        validate.validate()


def test_generated_credentials_meet_harbor_rules():
    with patch.object(credentials, 'htpasswd', return_value='harbor_registry_user:$2y$10$hash'):
        values = credentials.generate()
    assert set(values) == set(credentials.KEYS)
    assert len(values['secretKey']) == 16 and len(values['CSRF_KEY']) == 32
    admin = values['HARBOR_ADMIN_PASSWORD']
    assert re.search('[A-Z]', admin) and re.search('[a-z]', admin) and re.search('[0-9]', admin) and 8 <= len(admin) <= 128
    assert re.fullmatch('[0-9a-f]+', values['REDIS_PASSWORD'])   # embedded unescaped in redis:// URLs
    secrets_only = [v for k, v in values.items() if k != 'REGISTRY_HTPASSWD']
    assert len(set(secrets_only)) == len(secrets_only)


def test_htpasswd_reads_the_password_from_stdin():
    with patch.object(credentials.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'u:$2y$10$x\n')) as run:
        assert credentials.htpasswd('u', 'topsecret') == 'u:$2y$10$x'
    assert 'topsecret' not in ' '.join(run.call_args.args[0])
    assert run.call_args.kwargs['input'] == 'topsecret\n'
    assert run.call_args.args[0][:2] == ['htpasswd', '-niB']


def fake_kubectl(secret, pvcs=()):
    def check_output(command, text=True):
        if 'secret' in command:
            return json.dumps(secret) if secret else ''
        return json.dumps({'items': list(pvcs)})
    return check_output


def test_existing_credentials_are_reused():
    secret = {'data': {k: 'eA==' for k in credentials.KEYS}}
    with patch.object(credentials.subprocess, 'check_output', side_effect=fake_kubectl(secret)), \
            patch.object(credentials.subprocess, 'run') as run:
        credentials.ensure('mk4')
    run.assert_not_called()


def test_partial_credentials_are_not_rotated():
    with patch.object(credentials.subprocess, 'check_output', side_effect=fake_kubectl({'data': {'password': 'eA=='}})), \
            patch.object(credentials.subprocess, 'run') as run:
        with pytest.raises(RuntimeError, match='incomplete'):
            credentials.ensure('mk4')
    run.assert_not_called()


def test_missing_credentials_with_volumes_stop_before_writing():
    with patch.object(credentials.subprocess, 'check_output', side_effect=fake_kubectl(None, [{'metadata': {}}])), \
            patch.object(credentials.subprocess, 'run') as run:
        with pytest.raises(RuntimeError, match='Restore the original'):
            credentials.ensure('mk4')
    run.assert_not_called()


def test_new_credentials_go_to_kubectl_on_stdin():
    with patch.object(credentials.subprocess, 'check_output', side_effect=fake_kubectl(None)), \
            patch.object(credentials.shutil, 'which', return_value='/usr/sbin/htpasswd'), \
            patch.object(credentials, 'htpasswd', return_value='harbor_registry_user:$2y$10$hash'), \
            patch.object(credentials.subprocess, 'run') as run:
        credentials.ensure('mk4')
    command, kwargs = run.call_args.args[0], run.call_args.kwargs
    document = json.loads(kwargs['input'])
    assert command[-3:] == ['create', '-f', '-'] and '--context=mk4' in command
    assert document['metadata'] == {'name': 'harbor-credentials', 'namespace': 'harbor', 'labels': {'app.kubernetes.io/part-of': 'harbor'}}
    assert not any(value in ' '.join(command) for value in document['stringData'].values())
    assert kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL   # kubectl errors can echo the object


def test_create_secrets_offers_harbor(capsys):
    create = load('harbor_create_secrets', ROOT / 'scripts/setup/create_secrets.py')
    assert create.TARGETS['harbor'] == ('harbor', 'harbor-credentials')
    with patch.object(create.subprocess, 'run') as run:
        create.main(['--services', 'harbor'])
    run.assert_not_called()
    assert 'harbor/harbor-credentials: preserve existing' in capsys.readouterr().out


def test_traefik_reads_large_layers():
    values = yaml.safe_load((ROOT / 'deploy/minikube/traefik/values.yaml').read_text())
    timeout = values['ports']['websecure']['transport']['respondingTimeouts']['readTimeout']
    assert int(timeout.removesuffix('s')) >= 600
    assert 'transport' not in values['ports']['web']
