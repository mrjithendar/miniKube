"""deploy/minikube holds only mk4 objects; EKS lives in the tools repository (deploy/eks)."""
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
MINIKUBE = ROOT / 'deploy/minikube'


def documents(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def test_no_duplicate_resource_identities():
    seen = set()
    for path in MINIKUBE.rglob('*.yaml'):
        if 'templates' in path.parts or 'examples' in path.parts:
            continue
        for doc in documents(path):
            if isinstance(doc, dict) and doc.keys() >= {'apiVersion', 'kind', 'metadata'}:
                key = (doc['apiVersion'].split('/')[0], doc['kind'], doc['metadata'].get('namespace'), doc['metadata'].get('name'))
                assert key not in seen, (path, key)
                seen.add(key)


def test_deployment_commands_name_a_context():
    for path in (ROOT / 'README.md', MINIKUBE / 'README.md'):
        for line in path.read_text().splitlines():
            if re.match(r'\s*kubectl (apply|diff|wait|create|get)\b', line):
                raise AssertionError((path, line))


def test_no_command_applies_a_parent_directory():
    parents = ('deploy', 'deploy/minikube')
    for path in ROOT.joinpath('deploy').rglob('*.md'):
        for line in path.read_text().splitlines():
            if 'kubectl' in line and ' apply ' in line and '`' not in line:
                target = re.search(r'-[fk] (\S+)', line)
                assert not target or target.group(1).rstrip('/') not in parents, (path, line)


def minikube_objects():
    for path in MINIKUBE.rglob('*.yaml'):
        if 'templates' in path.parts:
            continue
        for doc in documents(path):
            if isinstance(doc, dict) and 'kind' in doc:
                yield path, doc


def test_minikube_has_no_aws_only_resources():
    text = re.compile(r'service\.beta\.kubernetes\.io/aws|elbv2\.k8s\.aws|eks\.amazonaws\.com|alb\.ingress\.kubernetes\.io|ebs\.csi|karpenter|\bgp3\b|loadBalancerClass')
    for path, doc in minikube_objects():
        assert not text.search(path.read_text()), path
        assert doc.get('spec', {}).get('ingressClassName') != 'alb' if isinstance(doc.get('spec'), dict) else True, path
        assert doc['spec'].get('storageClassName') not in ('gp2', 'gp3') if isinstance(doc.get('spec'), dict) else True, path


def test_minikube_applications_stay_on_the_local_cluster():
    for path, doc in minikube_objects():
        if doc['kind'] == 'Application':
            assert doc['spec']['destination']['server'] == 'https://kubernetes.default.svc', path
            assert 'deploy/eks' not in path.read_text(), path


def test_nothing_from_eks_or_the_aws_account_moved_here():
    for name in ('deploy/eks', 'infrastructure', 'devops-tools', 'eks-argocd', 'minikube', 'minikube-argocd', 'observability'):
        assert not (ROOT / name).exists(), name


def test_minikube_issuers_solve_dns01_for_the_wildcard():
    output = subprocess.check_output(['helm', 'template', 'letsencrypt-jithendar', str(MINIKUBE / 'cert-manager/issuers'),
                                      '-n', 'cert-manager'], text=True)
    docs = [d for d in yaml.safe_load_all(output) if d]
    issuers = {d['metadata']['name']: d for d in docs if d['kind'] == 'ClusterIssuer'}
    # letsencrypt-prod/-staging (HTTP-01) belong to the jithendar.com repository; these must not collide.
    assert set(issuers) == {'letsencrypt-dns', 'letsencrypt-dns-staging'}
    example = yaml.safe_load((MINIKUBE / 'cert-manager/examples/cloudflare-api-token-secret.yaml.example').read_text())
    # ClusterIssuers read their Secrets from cert-manager's own namespace.
    assert example['metadata']['namespace'] == 'cert-manager'
    for issuer in issuers.values():
        solver = issuer['spec']['acme']['solvers'][0]
        assert 'http01' not in solver and solver['selector']['dnsZones'] == ['jithendar.com']
        reference = solver['dns01']['cloudflare']['apiTokenSecretRef']
        assert reference == {'name': example['metadata']['name'], 'key': 'api-token'}
    certificate = next(d for d in docs if d['kind'] == 'Certificate')
    assert certificate['spec']['dnsNames'] == ['jithendar.com', '*.jithendar.com']
    assert certificate['spec']['issuerRef']['name'] == 'letsencrypt-dns'


def test_only_terraform_installs_traefik():
    # minikube-infrastructure/traefik.tf owns the release; a second installer would fight it over revisions and values.
    assert not (ROOT / 'deploy-traefik.sh').exists() and not (MINIKUBE / 'deploy-traefik.sh').exists()
    for path in [*ROOT.joinpath('scripts').rglob('*.py'), *ROOT.glob('*.sh'), *MINIKUBE.rglob('*.sh')]:
        text = path.read_text()
        assert not re.search(r"helm[^\n]*(install|upgrade)[^\n]*traefik|'traefik', 'https://traefik\.github\.io/charts'", text), path
