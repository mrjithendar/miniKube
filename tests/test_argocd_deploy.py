import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location('argocd_deploy', Path(__file__).resolve().parents[1] / 'scripts/argocd/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


# deploy.py's mechanics are tested against the catalog as it was before the add-ons moved to Terraform
# (fixtures/argocd-catalog/README.md); the live catalog holds no Applications by default.
FIXTURE_CATALOG = Path(__file__).resolve().parent / 'fixtures/argocd-catalog'


@pytest.fixture(autouse=True)
def no_external_writes():
    with patch.object(deploy.subprocess, 'check_call'), patch.object(deploy, 'CATALOGS', (FIXTURE_CATALOG,)):
        yield


def test_preview_never_touches_cluster(capsys):
    with patch('sys.argv', ['deploy', 'grafana', '--dry-run']), patch.object(deploy.subprocess, 'check_output') as run:
        deploy.main()
    run.assert_not_called()
    output = capsys.readouterr().out
    assert 'grafana' in output
    assert 'product-ingestion' not in output
    assert 'shopping-agent' not in output
    assert 'mongodb' not in output


def test_unknown_app_fails_before_cluster_access():
    with patch('sys.argv', ['deploy', 'loki', 'typo']), patch.object(deploy.subprocess, 'check_output') as run:
        with pytest.raises(SystemExit):
            deploy.main()
    run.assert_not_called()


def healthy(app):
    return {**app, 'status': {'operationState': {'phase': 'Succeeded'},
                              'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'},
                              'resources': [{'kind': 'Deployment', 'name': app['metadata']['name']}]}}


def test_existing_settings_preserved_and_no_pruning():
    # Matches Git except a live-only setting, which must survive untouched.
    app = deploy.catalog()[0]['loki']
    app['spec']['syncPolicy']['syncOptions'] = app['spec']['syncPolicy']['syncOptions'] + ['RespectIgnoreDifferences=true']
    listing = json.dumps({'items': [healthy(app)]})
    with patch('sys.argv', ['deploy', 'loki']), patch.object(deploy.subprocess, 'check_output',
            side_effect=['crd/applications', 'crd/appprojects', listing, 'appproject/mk-platform', 'patched\n', listing]) as run:
        deploy.main()
    commands = [call.args[0] for call in run.call_args_list]
    assert all('create' not in c and 'apply' not in c and '--type=json' not in c for c in commands)
    body = json.loads(run.call_args_list[4].kwargs['input'])
    assert set(body) == {'operation'}
    assert body['operation']['sync']['prune'] is False
    assert body['operation']['sync']['syncOptions'] == app['spec']['syncPolicy']['syncOptions']


def test_stale_live_source_aligned_with_git_before_sync():
    # A pre-split include renders nothing; a prune sync would delete everything.
    git = deploy.catalog()[0]['loki']
    stale = json.loads(json.dumps(git))
    stale['spec']['source']['path'] = 'deploy/minikube/observability/retired'
    stale['spec']['sources'] = [{'repoURL': 'https://example.invalid'}]
    responses = ['crd', 'crd', json.dumps({'items': [stale]}), 'appproject/mk-platform',
                 'patched', 'patched', json.dumps({'items': [healthy(git)]})]
    with patch('sys.argv', ['deploy', 'loki']), patch.object(deploy.subprocess, 'check_output', side_effect=responses) as run:
        deploy.main()
    patches = [c for c in run.call_args_list if 'patch' in c.args[0]]
    assert '--type=json' in patches[0].args[0]
    assert json.loads(patches[0].kwargs['input']) == [
        {'op': 'add', 'path': '/spec/source', 'value': git['spec']['source']},
        {'op': 'remove', 'path': '/spec/sources'}]
    assert 'operation' in json.loads(patches[1].kwargs['input'])


def test_destination_moves_are_aligned_and_use_git_namespace():
    git = deploy.catalog()[0]['prometheus']
    stale = json.loads(json.dumps(git))
    stale['spec']['destination']['namespace'] = 'roboshop-ai'
    responses = ['crd', 'crd', json.dumps({'items': [stale]}), 'appproject/mk-platform',
                 'patched', 'patched', json.dumps({'items': [healthy(git)]})]
    with patch('sys.argv', ['deploy', 'prometheus']), patch.object(deploy.subprocess, 'check_output', side_effect=responses) as run, \
            patch.object(deploy.subprocess, 'check_call') as apply:
        deploy.main()
    assert apply.call_args.args[0][-1].endswith('/deploy/minikube/namespaces/monitoring.yaml')
    ops = json.loads(next(c for c in run.call_args_list if '--type=json' in c.args[0]).kwargs['input'])
    assert ops == [{'op': 'add', 'path': '/spec/destination', 'value': git['spec']['destination']}]


def test_project_moves_create_git_project_then_align():
    git = deploy.catalog()[0]['jaeger']
    stale = json.loads(json.dumps(git))
    stale['spec']['project'] = 'mk-platform'
    responses = ['crd', 'crd', json.dumps({'items': [stale]}), '', 'created',
                 'patched', 'patched', json.dumps({'items': [healthy(git)]})]
    with patch('sys.argv', ['deploy', 'jaeger']), patch.object(deploy.subprocess, 'check_output', side_effect=responses) as run:
        deploy.main()
    calls = run.call_args_list
    assert 'appproject' in calls[3].args[0] and 'mk-observability' in calls[3].args[0]
    assert json.loads(calls[4].kwargs['input'])['metadata']['name'] == 'mk-observability'
    ops = json.loads(next(c for c in calls if '--type=json' in c.args[0]).kwargs['input'])
    assert ops == [{'op': 'add', 'path': '/spec/project', 'value': 'mk-observability'}]


def test_missing_git_sync_options_added_and_live_extras_kept():
    # Large ConfigMaps need ServerSideApply; a stale live policy must not drop it.
    git = deploy.yaml.safe_load((FIXTURE_CATALOG / 'cert-manager.yaml').read_text())
    stale = json.loads(json.dumps(git))
    stale['spec']['syncPolicy'] = {'syncOptions': ['FailOnSharedResource=true', 'RespectIgnoreDifferences=true'],
                                   'retry': {'limit': 2}}
    responses = ['crd', 'crd', json.dumps({'items': [stale]}), 'appproject/mk-platform',
                 'patched', 'patched', json.dumps({'items': [healthy(git)]})]
    with patch('sys.argv', ['deploy', 'cert-manager']), patch.object(deploy.subprocess, 'check_output', side_effect=responses) as run:
        deploy.main()
    patches = [c for c in run.call_args_list if 'patch' in c.args[0]]
    policy = json.loads(patches[0].kwargs['input'])[-1]
    assert policy['path'] == '/spec/syncPolicy' and policy['value']['retry'] == {'limit': 2}
    assert policy['value']['syncOptions'][:2] == ['FailOnSharedResource=true', 'RespectIgnoreDifferences=true']
    assert 'ServerSideApply=true' in policy['value']['syncOptions']
    sync = json.loads(patches[1].kwargs['input'])['operation']['sync']
    assert sync['syncOptions'] == policy['value']['syncOptions']


def test_synced_app_tracking_no_resources_fails():
    app = deploy.catalog()[0]['loki']
    empty = healthy(app); empty['status']['resources'] = []
    listing = json.dumps({'items': [empty]})
    with patch('sys.argv', ['deploy', 'loki']), patch.object(deploy.subprocess, 'check_output',
            side_effect=['crd', 'crd', listing, 'appproject/mk-platform', 'patched', listing]):
        with pytest.raises(RuntimeError, match='tracks no resources'):
            deploy.main()


def test_status_warns_about_drift_and_empty_apps(capsys):
    git = deploy.catalog()[0]['loki']
    stale = json.loads(json.dumps(git))
    stale['spec']['source']['path'] = 'deploy/minikube/observability/retired'
    good = healthy(deploy.catalog()[0]['grafana'])
    with patch('sys.argv', ['deploy', 'status']), patch.object(deploy.subprocess, 'check_output',
            side_effect=['crd', 'crd', 'NAME SYNC\n', json.dumps({'items': [stale, good]})]) as run:
        deploy.main()
    out = capsys.readouterr().out
    assert 'loki: live source/destination differs from Git' in out
    assert 'loki: tracks no resources' in out
    assert 'grafana' not in out.replace('NAME SYNC', '')
    assert not any('patch' in c.args[0] for c in run.call_args_list)


@pytest.mark.parametrize('arguments', [[], ['all']])
def test_default_and_all_include_entire_catalog(arguments, capsys):
    with patch('sys.argv', ['deploy', *arguments, '--dry-run']), patch.object(deploy.subprocess, 'check_output') as run:
        deploy.main()
    run.assert_not_called()
    output = capsys.readouterr().out
    apps, _ = deploy.catalog()
    assert 'action: ' + ' '.join(sorted(apps)) in output
    for name in ('grafana', 'loki', 'prometheus'):
        assert name in output

    for name in ( 'jfrog', 'product-ingestion', 'shopping-agent'):
        assert name not in output


def test_status_missing_crd_has_actionable_error_without_mutation():
    with patch('sys.argv', ['deploy', 'status']), patch.object(deploy.subprocess, 'check_output', return_value='') as run:
        with pytest.raises(RuntimeError, match='deploy-argocd.sh'):
            deploy.main()
    assert len(run.call_args_list) == 1
    assert 'get' in run.call_args.args[0]


def test_install_preview_is_offline():
    with patch('sys.argv', ['deploy', 'install', '--dry-run']), patch.object(deploy.subprocess, 'check_output') as read, patch.object(deploy.subprocess, 'check_call') as write:
        deploy.main()
    read.assert_not_called()
    write.assert_not_called()


def test_fresh_install_uses_pinned_chart_and_absolute_values():
    with patch('sys.argv', ['deploy', 'install', '--context', 'fresh']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=['  -A, --all-namespaces list across namespaces', '[]', '', 'established', 'established']), patch.object(deploy.subprocess, 'check_call') as write:
        deploy.main()
    command=write.call_args.args[0]
    assert 'fresh' in command
    assert deploy.ARGOCD_CHART_VERSION in command
    assert str(deploy.ROOT / 'deploy/minikube/argocd/values.yaml') in command
    assert '--wait' in command
    assert command[command.index('--repo') + 1] == 'https://argoproj.github.io/argo-helm'
    assert not any(arg.startswith('oci://') for arg in command)


def test_install_reuses_existing_release_without_upgrade():
    with patch('sys.argv', ['deploy', 'install']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=['  -a, --all show all releases', '[{"status":"deployed"}]', 'established', 'established']), patch.object(deploy.subprocess, 'check_call') as write:
        deploy.main()
    write.assert_not_called()


def test_install_does_not_adopt_unmanaged_crds():
    with patch('sys.argv', ['deploy', 'install']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=['', '[]', 'crd/applications']), patch.object(deploy.subprocess, 'check_call') as write:
        with pytest.raises(RuntimeError, match='Refusing automatic adoption'):
            deploy.main()
    write.assert_not_called()


@pytest.mark.parametrize('help_text,uses_all', [
    ('  -a, --all show all releases', True),
    ('      --all show all releases', True),
    ('  -A, --all-namespaces list all namespaces', False),
])
def test_helm_list_version_compatibility_and_pending_release(help_text, uses_all):
    with patch('sys.argv', ['deploy', 'install']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=[help_text, '[{"status":"pending-upgrade"}]']) as read, patch.object(deploy.subprocess, 'check_call') as write:
        with pytest.raises(RuntimeError, match='not deployed'):
            deploy.main()
    command = read.call_args_list[1].args[0]
    assert ('--all' in command) == uses_all
    assert '--namespace' in command
    assert '--all-namespaces' not in command
    write.assert_not_called()


def test_missing_controller_installed_before_application_sync():
    healthy = {'metadata': {'name': 'loki'}, 'spec': {'project': 'mk-platform'},
               'status': {'operationState': {'phase': 'Succeeded'},
                          'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'},
                          'resources': [{'kind': 'Deployment', 'name': 'loki'}]}}
    responses = ['', '--all-namespaces', '[]', '', 'established', 'established',
                 '{"items":[]}', 'appproject/mk-platform', 'created', 'patched',
                 json.dumps({'items': [healthy]})]
    with patch('sys.argv', ['deploy', 'loki', '--context', 'fresh']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=responses) as read, patch.object(deploy.subprocess, 'check_call') as install:
        deploy.main()
    assert len([c for c in install.call_args_list if c.args[0][0] == 'helm']) == 1
    assert all('fresh' in c.args[0] for c in install.call_args_list)
    assert any('patch' in call.args[0] for call in read.call_args_list)


def test_failed_automatic_install_never_deploys_apps():
    with patch('sys.argv', ['deploy', 'loki']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=['', '', '[]', '']) as read, patch.object(deploy.subprocess, 'check_call', side_effect=deploy.subprocess.CalledProcessError(1, 'helm')):
        with pytest.raises(deploy.subprocess.CalledProcessError):
            deploy.main()
    assert not any('patch' in call.args[0] or 'create' in call.args[0] for call in read.call_args_list)


@pytest.mark.parametrize('existing', [False, True])
def test_cert_manager_sync_carries_namespace_and_ssa_options(existing):
    app = deploy.yaml.safe_load((FIXTURE_CATALOG / 'cert-manager.yaml').read_text())
    options = app['spec']['syncPolicy']['syncOptions']
    if existing:
        options.append('RespectIgnoreDifferences=true')
    app['status'] = {'operationState': {'phase': 'Succeeded'},
                     'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'},
                     'resources': [{'kind': 'Deployment', 'name': 'loki'}]}
    responses = ['crd/applications', 'crd/appprojects', json.dumps({'items': [app] if existing else []}), 'appproject/mk-platform']
    if not existing:
        responses.append('created')
    responses += ['patched', json.dumps({'items': [app]})]
    with patch('sys.argv', ['deploy', 'cert-manager']), patch.object(deploy.subprocess, 'check_output', side_effect=responses) as run:
        deploy.main()
    call = next(c for c in run.call_args_list if 'patch' in c.args[0])
    sync = json.loads(call.kwargs['input'])['operation']['sync']
    assert sync['syncOptions'] == options
    assert 'CreateNamespace=true' in sync['syncOptions']
    assert 'ServerSideApply=true' in sync['syncOptions']
    assert sync['prune'] is False


def test_pending_install_reused_only_after_workload_and_crd_checks():
    with patch('sys.argv', ['deploy', 'install']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=['', '[{"status":"pending-install"}]'] + ['ready'] * 6) as read, patch.object(deploy.subprocess, 'check_call') as write:
        deploy.main()
    commands = [c.args[0] for c in read.call_args_list]
    assert len([c for c in commands if 'rollout' in c]) == 4
    assert len([c for c in commands if 'wait' in c]) == 2
    write.assert_not_called()


def test_unhealthy_pending_install_stops_without_release_mutation():
    with patch('sys.argv', ['deploy', 'install']), patch.object(deploy.shutil, 'which', return_value='/bin/helm'), patch.object(deploy.subprocess, 'check_output', side_effect=['', '[{"status":"pending-install"}]', deploy.subprocess.CalledProcessError(1, 'kubectl')]), patch.object(deploy.subprocess, 'check_call') as write:
        with pytest.raises(deploy.subprocess.CalledProcessError):
            deploy.main()
    write.assert_not_called()


def test_namespace_applied_before_sync_and_deduplicated():
    app = deploy.catalog()[0]['loki']
    app['status'] = {'operationState': {'phase': 'Succeeded'}, 'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'},
                     'resources': [{'kind': 'Deployment', 'name': 'loki'}]}
    events = []
    responses = iter(['crd', 'crd', json.dumps({'items':[app]}), 'project', 'patched', json.dumps({'items':[app]})])
    def read(command, **kwargs):
        events.append(command)
        return next(responses)
    with patch('sys.argv', ['deploy', 'loki', 'loki']), patch.object(deploy.subprocess, 'check_output', side_effect=read), patch.object(deploy.subprocess, 'check_call', side_effect=lambda c: events.append(c)):
        deploy.main()
    applies = [c for c in events if 'apply' in c]
    assert len(applies) == 1
    assert applies[0][-1].endswith('/deploy/minikube/namespaces/monitoring.yaml')
    assert '--server-side' in applies[0]
    assert events.index(applies[0]) < next(i for i,c in enumerate(events) if 'patch' in c)


def test_all_catalog_destinations_have_namespace_manifests():
    for app in deploy.catalog()[0].values():
        namespace = app['spec']['destination']['namespace']
        doc = deploy.yaml.safe_load((deploy.ROOT / 'deploy/minikube/namespaces' / f'{namespace}.yaml').read_text())
        assert doc == {'apiVersion':'v1', 'kind':'Namespace', 'metadata':{'name':namespace}}
