import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('refresh_job',ROOT/'deploy/minikube/ecr-refresh/refresh.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
TOKEN=base64.b64encode(b'AWS:private-password').decode()


def test_updates_named_secret_preserves_other_registries_and_redacts(capsys):
    existing={'type':'kubernetes.io/dockerconfigjson','metadata':{'resourceVersion':'7'},'data':{'.dockerconfigjson':base64.b64encode(json.dumps({'auths':{'other':{'auth':'keep'}}}).encode()).decode()}}
    account={'metadata':{'resourceVersion':'3'},'imagePullSecrets':[{'name':'ecr-pull'}]}
    request=Mock(side_effect=[existing,{},account])
    module.refresh('registry',['roboshop-ai'],request,TOKEN)
    method,path,body=request.call_args_list[1].args
    assert method=='PATCH' and path.endswith('/secrets/ecr-pull')
    assert body['metadata']['resourceVersion']=='7'
    config=json.loads(base64.b64decode(body['data']['.dockerconfigjson']))
    assert config['auths']=={'other':{'auth':'keep'},'registry':{'auth':TOKEN}}
    output=capsys.readouterr().out
    assert TOKEN not in output and 'private-password' not in output


def test_failure_does_not_delete_and_is_nonzero(capsys):
    request=Mock(side_effect=RuntimeError('sensitive-response'))
    with pytest.raises(RuntimeError):
        module.refresh('registry',['a','b'],request,TOKEN)
    assert all(c.args[0]=='GET' for c in request.call_args_list)
    assert 'sensitive-response' not in capsys.readouterr().out


def test_invalid_token_never_contacts_kubernetes():
    request=Mock()
    with pytest.raises(Exception): module.refresh('registry',['a'],request,'invalid')
    request.assert_not_called()


def test_schedule_rbac_and_public_images():
    docs=[yaml.safe_load(p.read_text()) for p in (ROOT/'deploy/minikube/ecr-refresh').glob('*.yaml') if p.name != 'kustomization.yaml']
    cron=next(d for d in docs if d['kind']=='CronJob')['spec']
    assert cron['schedule']=='0 */6 * * *' and cron['concurrencyPolicy']=='Forbid'
    assert cron['jobTemplate']['spec']['activeDeadlineSeconds']==300
    pod=cron['jobTemplate']['spec']['template']['spec']
    assert not pod['automountServiceAccountToken']
    assert all('.dkr.ecr.' not in c['image'] for c in pod['containers']+pod['initContainers'])
    assert not any(m['name']=='api' for m in pod['initContainers'][0]['volumeMounts'])
    assert not any(d['kind'] in ('Role','RoleBinding') for d in docs)
    role=next(d for d in docs if d['kind']=='ClusterRole')
    assert role['rules']==[
        {'apiGroups':[''],'resources':['namespaces'],'verbs':['list']},
        {'apiGroups':[''],'resources':['secrets'],'verbs':['create']},
        {'apiGroups':[''],'resources':['secrets'],'resourceNames':['ecr-pull'],'verbs':['get','patch']},
        {'apiGroups':[''],'resources':['serviceaccounts'],'resourceNames':['default'],'verbs':['get','patch']},
    ]
    binding=next(d for d in docs if d['kind']=='ClusterRoleBinding')
    assert binding['subjects']==[{'kind':'ServiceAccount','name':'ecr-refresh','namespace':'roboshop-ai'}]
    env={e['name']:e['value'] for e in pod['containers'][0]['env']}
    assert 'TARGET_NAMESPACES' not in env
    assert set(env['EXCLUDED_NAMESPACES'].split(','))=={'default','kube-system','kube-public','kube-node-lease'}


def test_targets_all_active_namespaces_except_excluded():
    listing={'items':[{'metadata':{'name':n},'status':{'phase':p}} for n,p in
        (('kube-system','Active'),('default','Active'),('moks','Active'),('argocd','Active'),('old','Terminating'))]}
    request=Mock(return_value=listing)
    assert module.target_namespaces(request,{'default','kube-system'})==['argocd','moks']
    request.assert_called_once_with('GET','/api/v1/namespaces')


def test_creates_missing_secret_and_attaches_it_to_default_service_account(capsys):
    account={'metadata':{'resourceVersion':'5'},'imagePullSecrets':[{'name':'other'}]}
    request=Mock(side_effect=[None,{},account,{}])
    module.refresh('registry',['new-app'],request,TOKEN)
    calls=[c.args for c in request.call_args_list]
    assert calls[1][:2]==('POST','/api/v1/namespaces/new-app/secrets')
    secret=calls[1][2]
    assert secret['metadata']['name']=='ecr-pull' and secret['type']=='kubernetes.io/dockerconfigjson'
    assert json.loads(base64.b64decode(secret['data']['.dockerconfigjson']))=={'auths':{'registry':{'auth':TOKEN}}}
    assert calls[3]==('PATCH','/api/v1/namespaces/new-app/serviceaccounts/default',
                      {'metadata':{'resourceVersion':'5'},'imagePullSecrets':[{'name':'other'},{'name':'ecr-pull'}]})
    output=capsys.readouterr().out
    assert 'new-app: ecr-pull created' in output and TOKEN not in output


def test_missing_default_service_account_is_not_a_failure():
    request=Mock(side_effect=[None,{},None])
    module.refresh('registry',['brand-new'],request,TOKEN)
    assert request.call_count==3
