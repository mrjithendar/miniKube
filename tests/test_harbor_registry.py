"""Harbor project, robots and pull secrets (deploy/minikube/harbor/registry.py); no network, cluster or GitHub."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('harbor_registry', ROOT / 'deploy/minikube/harbor/registry.py')
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


class FakeHarbor(registry.Harbor):
    """Harbor without kubectl or HTTP: records calls and answers from `responses` (method, path) -> (status, body)."""

    def __init__(self, responses):
        self.context, self.calls, self.responses = 'mk4', [], responses

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.responses.get((method, path), (201, None))


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        missing = args[-2:] == ['namespace', 'absent']
        return subprocess.CompletedProcess(args, 1 if missing else 0, '', '')
    monkeypatch.setattr(registry.subprocess, 'run', run)
    return calls


def test_one_private_project():
    harbor = FakeHarbor({})
    registry.project(harbor)
    assert harbor.calls == [('POST', '/projects', {'project_name': 'jithendar', 'metadata': {'public': 'false'}})]


def test_ci_robots_push_and_pull_one_project_and_secrets_go_to_gh_on_stdin(runs):
    harbor = FakeHarbor({('POST', '/robots'): (201, {'secret': 's3cr3t'})})
    registry.ci_robots(harbor, rotate=False)
    bodies = [body for method, path, body in harbor.calls if path == '/robots']
    assert {b['name'] for b in bodies} == {'moks', 'roboshop-ai', 'jithendar-com'}
    for body in bodies:
        assert body['level'] == 'project' and body['duration'] == 365
        assert body['permissions'] == [{'kind': 'project', 'namespace': 'jithendar', 'access': [
            {'resource': 'repository', 'action': 'push'}, {'resource': 'repository', 'action': 'pull'}]}]
    sets = [(args, kwargs['input']) for args, kwargs in runs]
    assert (['gh', 'secret', 'set', 'HARBOR_PASSWORD', '-R', 'mrjithendar/moks'], 's3cr3t') in sets
    assert (['gh', 'secret', 'set', 'HARBOR_USERNAME', '-R', 'mrjithendar/moks'], 'robot$jithendar+moks') in sets
    assert not any('s3cr3t' in arg for args, _ in runs for arg in args)


def test_an_existing_robot_keeps_its_secret_unless_rotated(runs):
    harbor = FakeHarbor({('POST', '/robots'): (409, None)})
    registry.ci_robots(harbor, rotate=False, only=('moks',))
    assert runs == [] and [c[0] for c in harbor.calls] == ['POST']
    harbor = FakeHarbor({('POST', '/robots'): (409, None),
                         ('GET', '/robots?page_size=100'): (200, [{'id': 7, 'name': 'robot$jithendar+moks'}]),
                         ('PATCH', '/robots/7'): (200, {'secret': 'new'})})
    registry.ci_robots(harbor, rotate=True, only=('moks',))
    assert ('PATCH', '/robots/7', {}) in harbor.calls
    assert ('new' in [kwargs['input'] for _, kwargs in runs])


def test_pull_secret_is_a_read_only_system_robot_that_never_expires(runs):
    harbor = FakeHarbor({('POST', '/robots'): (201, {'secret': 'pull-secret'})})
    registry.pull_secrets(harbor, ['moks', 'absent'], rotate=False)
    body = harbor.calls[0][2]
    assert body['name'] == 'k8s-pull' and body['level'] == 'system' and body['duration'] == -1
    assert body['permissions'][0]['access'] == [{'resource': 'repository', 'action': 'pull'}]
    applied = [json.loads(kwargs['input']) for args, kwargs in runs if 'apply' in args]
    assert [d['metadata']['namespace'] for d in applied] == ['moks']   # a missing namespace is skipped
    assert not any('pull-secret' in arg for args, _ in runs for arg in args)


def test_pull_document_is_a_docker_config_for_harbor_only():
    document = registry.pull_document('jithendar', 'xyz')
    assert document['type'] == 'kubernetes.io/dockerconfigjson' and document['metadata']['name'] == 'harbor-pull'
    config = json.loads(base64.b64decode(document['data']['.dockerconfigjson']))
    assert list(config['auths']) == ['registry.jithendar.com']
    assert base64.b64decode(config['auths']['registry.jithendar.com']['auth']).decode() == 'robot$k8s-pull:xyz'


def test_an_existing_pull_robot_asks_for_its_secret(runs):
    harbor = FakeHarbor({('POST', '/robots'): (409, None)})
    registry.pull_secrets(harbor, ['moks'], rotate=False, prompt=lambda _: 'typed')
    applied = [json.loads(kwargs['input']) for args, kwargs in runs if 'apply' in args]
    auth = json.loads(base64.b64decode(applied[0]['data']['.dockerconfigjson']))['auths']['registry.jithendar.com']['auth']
    assert base64.b64decode(auth).decode() == 'robot$k8s-pull:typed'


def test_conflict_is_returned_and_other_errors_raise_without_the_body():
    def opener(status):
        def open_(request, timeout):
            raise urllib.error.HTTPError(request.full_url, status, 'x', {}, io.BytesIO(b'{"errors":"secret detail"}'))
        return open_
    harbor = registry.Harbor.__new__(registry.Harbor)
    harbor.headers = {}
    harbor.opener = opener(409)
    assert harbor.call('POST', '/projects', {}) == (409, None)
    harbor.opener = opener(401)
    with pytest.raises(RuntimeError, match='HTTP 401') as error:
        harbor.call('GET', '/robots')
    assert 'secret detail' not in str(error.value)


def test_every_repository_image_is_copied_and_harbor_itself_is_not():
    assert 'jithendar-com' in registry.IMAGES and 'moks-terminal' in registry.IMAGES
    assert 'roboshop-shipping' in registry.IMAGES and 'roboshop-shipping-service' not in registry.IMAGES
    assert not any('harbor' in image for image in registry.IMAGES)
