import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess
import pytest

spec = importlib.util.spec_from_file_location('ecr_bootstrap', Path(__file__).resolve().parents[1] / 'scripts/argocd/bootstrap.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_ecr_credentials_stdin_only_and_namespaces(capsys):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, 'private-test-token' if command[0] == 'aws' else '', '')
    with patch.dict(module.os.environ, {}, clear=True), patch.object(module.subprocess, 'run', side_effect=run), patch.object(module.subprocess, 'check_call'):
        module.refresh_pull_secrets('test-context')
    secrets = [json.loads(k['input']) for c,k in calls if 'input' in k and json.loads(k['input'])['kind'] == 'Secret']
    expected = {p.stem for p in (module.ROOT/'deploy/minikube/namespaces').glob('*.yaml')} - {'kube-system'}
    assert {d['metadata']['namespace'] for d in secrets} == expected
    for secret in secrets:
        config = json.loads(base64.b64decode(secret['data']['.dockerconfigjson']))
        auth = next(iter(config['auths'].values()))['auth']
        assert base64.b64decode(auth).decode() == 'AWS:private-test-token'
    assert all('private-test-token' not in ' '.join(c) for c,k in calls)
    assert 'private-test-token' not in capsys.readouterr().out
    accounts = [json.loads(k['input']) for c,k in calls if 'input' in k and json.loads(k['input'])['kind'] == 'ServiceAccount']
    assert all(d['imagePullSecrets'] == [{'name':'ecr-pull'}] for d in accounts)


def test_aws_failure_never_mutates_cluster():
    with patch.dict(module.os.environ, {}, clear=True), patch.object(module.subprocess, 'run', return_value=CompletedProcess(['aws'], 1, '', 'sensitive')) as run, patch.object(module.subprocess, 'check_call') as write:
        with pytest.raises(RuntimeError, match='Cannot obtain ECR'):
            module.refresh_pull_secrets('mk4')
    assert run.call_count == 1
    write.assert_not_called()
