"""Harbor on mk4 (deploy/minikube/harbor): offline contracts. Terraform installs it (minikube-infrastructure/harbor.tf)."""
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
VALUES = yaml.safe_load((HARBOR / 'values.yaml').read_text())
SCRIPT = (HARBOR / 'deploy-harbor.sh').read_text()
TERRAFORM = (ROOT / 'minikube-infrastructure/harbor.tf').read_text()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


# harbor-credentials, as harbor.tf generates it: the keys of local.harbor_credential_lengths plus the htpasswd line.
TF_CREDENTIAL_KEYS = set(re.findall(r'^    (\w+)\s+= \d+$', TERRAFORM.split('harbor_credential_lengths = {')[1].split('}')[0], re.M)) | {'REGISTRY_HTPASSWD'}
TF_REGISTRY_USER = re.search(r'harbor_registry_user = "([^"]+)"', TERRAFORM).group(1)
deploy = load('harbor_deploy', ROOT / 'scripts/argocd/deploy.py')


def manifests():
    """Harbor's objects outside the chart: the harbor-access release (minikube-infrastructure/harbor.tf)."""
    rendered = subprocess.check_output(['helm', 'template', 'harbor-access', str(HARBOR / 'access'), '--namespace', 'harbor'], text=True)
    return [d for d in yaml.safe_load_all(rendered) if d]


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


def test_harbor_is_a_terraform_release_not_an_argo_cd_application():
    assert 'harbor' not in deploy.catalog()[0] and 'harbor' not in deploy.optional_catalog()[0]
    assert not (ROOT / 'deploy/minikube/applications/optional/harbor.yaml').exists()
    assert not (HARBOR / 'manifests').exists()
    assert 'repository = "https://helm.goharbor.io"' in TERRAFORM and 'version    = "1.19.4"' in TERRAFORM
    assert 'chart     = "${local.minikube}/harbor/access"' in TERRAFORM
    # No script runs first: Terraform creates the namespace (in the mesh) and both Secrets.
    assert 'resource "kubernetes_namespace_v1" "harbor"' in TERRAFORM and '"istio-injection"           = "enabled"' in TERRAFORM
    assert 'resource "kubernetes_secret_v1" "harbor_credentials"' in TERRAFORM
    assert 'resource "kubernetes_secret_v1" "harbor_dockerhub_pull"' in TERRAFORM
    assert 'harbor' not in load('harbor_create_secrets', ROOT / 'scripts/setup/create_secrets.py').TARGETS
    assert not (HARBOR / 'credentials.py').exists()


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


def test_no_node_pins_volumes_decide_and_jobs_follow_the_registry():
    # local-path volumes keep each pod on the worker that holds them; no hostname survives a cluster rebuild.
    assert not any('nodeSelector' in VALUES[c] for c in ('portal', 'core', 'jobservice', 'registry', 'trivy'))
    assert VALUES['persistence']['persistentVolumeClaim']['registry']['storageClass'] == 'local-path'
    for name, spec in pod_specs():
        assert 'nodeSelector' not in spec, name
        term = spec['affinity']['podAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
        assert term['labelSelector']['matchLabels'] == {'app': 'harbor', 'component': 'registry'}, name


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
    assert VALUES['imagePullSecrets'] == [{'name': re.search(r'name      = "(dockerhub-pull)"', TERRAFORM).group(1)}]
    assert VALUES['metrics']['enabled'] is False and VALUES['trivy']['enabled'] is False


def test_script_pins_source_refuses_the_cluster_host_and_leaves_the_deploy_to_terraform():
    assert re.search(r'^HARBOR_COMMIT=[0-9a-f]{40}\b', SCRIPT, re.M)
    assert '[ "$(git -C "$WORK/harbor" rev-parse HEAD)" = "$HARBOR_COMMIT" ]' in SCRIPT
    assert "grep -qx mk4" in SCRIPT and "'{{.Server.Arch}}')\" = arm64" in SCRIPT
    assert 'PULL_BASE_FROM_DOCKERHUB=false' in SCRIPT and 'make/photon/common/Dockerfile' in SCRIPT
    assert 'terraform -chdir="$ROOT/minikube-infrastructure" apply -var-file=vars/mk4.tfvars' in SCRIPT
    assert 'create-secrets' not in SCRIPT
    assert 'scripts/argocd/deploy.py' not in SCRIPT
    # The Docker Hub token reaches Terraform through the environment, never argv.
    assert 'TF_VAR_dockerhub_password' in SCRIPT and '--docker-password' not in SCRIPT
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
    secret = 'harbor-credentials'
    assert VALUES['existingSecretAdminPassword'] == VALUES['existingSecretSecretKey'] == secret
    assert VALUES['core']['existingSecret'] == VALUES['core']['existingXsrfSecret'] == secret
    assert VALUES['jobservice']['existingSecret'] == VALUES['registry']['existingSecret'] == secret
    assert VALUES['registry']['credentials']['existingSecret'] == secret
    # The database password is the shared PostgreSQL tenant's (postgres.tf), not Harbor's own.
    assert VALUES['database']['external']['existingSecret'] == 'postgres-db-credentials'
    assert VALUES['core']['secretName'] == by_name('Certificate')['harbor-token']['spec']['secretName']
    assert VALUES['registry']['credentials']['username'] == TF_REGISTRY_USER
    # Chart-defined key names (values.yaml comments) plus every secretKeyRef: exactly what harbor.tf writes.
    chart_keys = {VALUES['existingSecretAdminPasswordKey'], 'secretKey', 'secret', VALUES['core']['existingXsrfSecretKey'],
                  VALUES['jobservice']['existingSecretKey'], VALUES['registry']['existingSecretKey'],
                  'REGISTRY_PASSWD', 'REGISTRY_HTPASSWD'}
    assert chart_keys | references_to(secret) == TF_CREDENTIAL_KEYS
    # Never set in Git.
    assert 'harborAdminPassword' not in VALUES and 'secretKey' not in VALUES
    assert VALUES['redis']['external']['password'] == '' and 'existingSecret' not in VALUES['redis']['external']
    assert VALUES['database']['type'] == VALUES['redis']['type'] == 'external'


def test_database_is_the_shared_postgres_through_pgbouncer():
    db = VALUES['database']
    assert db['external']['host'] == 'postgres-cluster-pooler-rw.postgres-db.svc.cluster.local'
    assert db['external']['username'] == db['external']['coreDatabase'] == 'registry'   # tenant "registry"
    assert db['external']['sslmode'] == 'require'   # postgres-db has no sidecars: TLS is the only encryption
    assert db['maxOpenConns'] * 2 <= 50   # core + jobservice under the role's connection_limit


@pytest.mark.parametrize('component,names', [
    ('core', ['_REDIS_URL_CORE', '_REDIS_URL_REG']),
    ('jobservice', ['_REDIS_URL_CORE', 'JOB_SERVICE_POOL_REDIS_URL']),
])
def test_redis_urls_carry_the_password_at_runtime(component, names):
    env = VALUES[component]['extraEnvVars']
    order = [e['name'] for e in env]
    assert order[0] == 'REDIS_PASSWORD'   # $(VAR) only expands variables defined earlier in the list
    assert env[0]['valueFrom'] == {'secretKeyRef': {'name': 'redis-harbor-credentials', 'key': 'password'}}
    addr = VALUES['redis']['external']['addr']
    assert addr == 'valkey-master.redis.svc.cluster.local:6379'
    for name in names:
        value = next(e['value'] for e in env if e['name'] == name)
        assert value.startswith(f'redis://harbor:$(REDIS_PASSWORD)@{addr}/'), value
    indexes = {'_REDIS_URL_CORE': 'coreDatabaseIndex', '_REDIS_URL_REG': 'registryDatabaseIndex', 'JOB_SERVICE_POOL_REDIS_URL': 'jobserviceDatabaseIndex'}
    for name in names:
        value = next(e['value'] for e in env if e['name'] == name)
        assert value.split('/')[3].split('?')[0] == VALUES['redis']['external'][indexes[name]]


def test_registry_containers_get_the_redis_password():
    for container in ('registry', 'controller'):
        env = VALUES['registry'][container]['extraEnvVars']
        assert env == [{'name': 'REGISTRY_REDIS_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'redis-harbor-credentials', 'key': 'password'}}}]
    assert VALUES['redis']['external']['username'] == 'harbor'


def test_redis_is_the_shared_valkey_within_the_harbor_tenant():
    # minikube-infrastructure/vars/mk4.tfvars: redis_tenants.harbor = databases [2, 3, 4, 5]
    external = VALUES['redis']['external']
    indexes = {external[k] for k in ('coreDatabaseIndex', 'jobserviceDatabaseIndex', 'registryDatabaseIndex', 'trivyAdapterIndex')}
    assert indexes == {'2', '3', '4', '5'}
    assert not [d for d in manifests() if 'redis' in d['metadata']['name']]   # no Redis of Harbor's own


def test_registry_volume_survives_uninstall():
    assert VALUES['persistence']['resourcePolicy'] == 'keep'


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
    # harbor-credentials is never read; pg_dump reads only the database tenant's login, straight to the primary.
    refs = {(r['secretKeyRef']['name'], r['secretKeyRef']['key']) for c in pod['initContainers'] + pod['containers']
            for e in c.get('env', []) for r in [e.get('valueFrom', {})] if 'secretKeyRef' in r}
    assert not {k for n, k in refs if n == 'harbor-credentials'}
    assert {k for n, k in refs if n == 'postgres-db-credentials'} == {'direct-host', 'username', 'dbname', 'password'}

