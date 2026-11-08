import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[1] / 'scripts/argocd/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture(autouse=True)
def mock_credentials():
    with patch.object(bootstrap, 'refresh_pull_secrets'), patch.object(bootstrap, 'ensure_app_secrets'):
        yield


def test_preview_is_offline():
    with patch('sys.argv', ['bootstrap', '--dry-run']), patch.object(bootstrap.subprocess, 'check_output') as read, patch.object(bootstrap.subprocess, 'check_call') as write:
        bootstrap.main()
    read.assert_not_called()
    write.assert_not_called()


TRAEFIK_PRESENT = ['namespace/kube-system', 'ingressclass.networking.k8s.io/traefik',
                   'customresourcedefinition.apiextensions.k8s.io/middlewares.traefik.io']


def test_fresh_install_order_and_context():
    with patch('sys.argv', ['bootstrap', '--context', 'fresh', '--deploy-apps']), patch.object(bootstrap.shutil, 'which', return_value='/bin/tool'), patch.object(bootstrap.subprocess, 'check_output', side_effect=TRAEFIK_PRESENT) as read, patch.object(bootstrap.subprocess, 'check_call') as write:
        bootstrap.main()
    commands = [call.args[0] for call in write.call_args_list]
    # Traefik and cert-manager are Terraform releases (minikube-infrastructure/), never installed here.
    assert not [c for c in commands if c[0] == 'helm']
    assert [c for c in commands if 'install' in c][0][1].endswith('scripts/argocd/deploy.py')
    assert commands[1][-2:] == ['-k', str(bootstrap.ROOT / 'deploy/minikube/argocd')]
    assert 'install' not in commands[-1]
    assert all('fresh' in c for c in commands + [call.args[0] for call in read.call_args_list])


@pytest.mark.parametrize('missing', [1, 2])
def test_missing_traefik_stops_before_argo_cd(missing):
    found = list(TRAEFIK_PRESENT)
    found[missing] = ''
    with patch('sys.argv', ['bootstrap']), patch.object(bootstrap.shutil, 'which', return_value='/bin/tool'), patch.object(bootstrap.subprocess, 'check_output', side_effect=found), patch.object(bootstrap.subprocess, 'check_call') as write:
        with pytest.raises(RuntimeError, match='Traefik is a Terraform release'):
            bootstrap.main()
    write.assert_not_called()


def test_install_failure_stops_later_stages():
    with patch('sys.argv', ['bootstrap', '--deploy-apps']), patch.object(bootstrap.shutil, 'which', return_value='/bin/tool'), patch.object(bootstrap.subprocess, 'check_output', side_effect=TRAEFIK_PRESENT), patch.object(bootstrap.subprocess, 'check_call', side_effect=bootstrap.subprocess.CalledProcessError(1, 'deploy.py')) as write:
        with pytest.raises(bootstrap.subprocess.CalledProcessError):
            bootstrap.main()
    assert write.call_count == 1


def test_pull_secret_refresh_only_skips_controllers():
    with patch('sys.argv', ['bootstrap', '--pull-secrets-only']), patch.object(bootstrap.shutil, 'which', return_value='/bin/tool'), patch.object(bootstrap, 'refresh_pull_secrets') as refresh, patch.object(bootstrap.subprocess, 'check_output') as read:
        bootstrap.main()
    refresh.assert_called_once_with('mk4')
    read.assert_not_called()


# The autouse fixture mocks ensure_app_secrets; keep the real one for these tests.
_ensure_app_secrets = bootstrap.ensure_app_secrets


def _secret_calls(claims):
    # namespace apply (check_call), then: existing Secret lookup, PVC names.
    return ['', claims]


def test_new_credentials_refused_when_database_volumes_exist():
    with patch.object(bootstrap.subprocess, 'check_call'), \
         patch.object(bootstrap.subprocess, 'check_output', side_effect=_secret_calls('mongodb-data gateway-data')), \
         patch.object(bootstrap.subprocess, 'run') as create:
        with pytest.raises(RuntimeError, match='data volumes exist: mongodb-data'):
            _ensure_app_secrets('mk4')
    create.assert_not_called()


def test_new_credentials_created_on_fresh_cluster_or_when_accepted():
    for claims, accept in (('gateway-data', False), ('mongodb-data shipping-postgres', True)):
        with patch.object(bootstrap.subprocess, 'check_call'), \
             patch.object(bootstrap.subprocess, 'check_output', side_effect=_secret_calls(claims)), \
             patch.object(bootstrap.subprocess, 'run', return_value=bootstrap.subprocess.CompletedProcess([], 0)) as create:
            _ensure_app_secrets('mk4', accept_new_credentials=accept)
        assert create.called
