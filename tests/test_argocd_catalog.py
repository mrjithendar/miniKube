"""Ownership and adoption contracts; no cluster mutations or network downloads."""
import importlib.util
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from repos import APP_REPO, ARGOCD, MINIKUBE, checkout  # noqa: E402
APP = checkout(APP_REPO)  # roboshop-ai: microservices and their manifests
# Helm releases in minikube-infrastructure/ that used to be Argo CD Applications, and the namespaces they own.
ADDONS=('metrics-server','cert-manager','grafana','prometheus','loki','otel-collector','jaeger')
TERRAFORM_NAMESPACES={'kube-system','cert-manager','istio-system','monitoring','observability'}
spec=importlib.util.spec_from_file_location('catalog_validate',ROOT/'scripts/argocd/validate.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_catalog_has_unique_safe_ownership():
    apps,owners=module.validate()
    names={app['metadata']['name'] for app in apps}
    assert not {'moks','catalogue-service','web'} & names
    assert not {'shopping-agent','product-ingestion','jfrog'} & names
    assert not {'argocd','traefik','coredns','kube-proxy','kindnet'}&names
    # deploy/minikube/applications holds no Application: the add-ons, Argo CD and Harbor are Terraform releases
    # (minikube-infrastructure/) and RoboShop is on EKS (roboshop-ai/argocd-eks).
    assert names==set()
    assert not any(ns=='roboshop-ai' and kind in ('Deployment','StatefulSet') for _,kind,ns,_ in owners)


def test_catalog_contains_only_projects_and_applications():
    assert all(doc['kind'] in ('Application','AppProject') for doc in module.catalog())


def test_adoption_baselines_have_no_credentials_or_server_metadata():
    for path in (ROOT/'deploy/minikube/gitops/adoption').glob('*/resources.yaml'):
        for doc in yaml.safe_load_all(path.read_text()):
            assert doc['kind']!='Secret'
            assert 'status' not in doc
            assert not {'uid','resourceVersion','managedFields','ownerReferences'}&doc['metadata'].keys()
            assert 'Prune=false' in doc['metadata']['annotations']['argocd.argoproj.io/sync-options']
            assert 'Delete=false' in doc['metadata']['annotations']['argocd.argoproj.io/sync-options']
            for container in doc.get('spec',{}).get('template',{}).get('spec',{}).get('containers',[]):
                for env in container.get('env',[]):
                    if any(term in env['name'].upper() for term in ('PASSWORD','TOKEN','SECRET','API_KEY')):
                        assert 'value' not in env,(path,env['name'])


def test_helm_releases_keep_names_and_pinned_versions():
    for doc in module.catalog():
        if doc['kind']!='Application':continue
        for source in doc['spec'].get('sources',[]):
            if 'chart' not in source:continue
            assert source['targetRevision'] not in ('HEAD','master','latest','*')
            assert source['helm']['releaseName']==doc['metadata']['name']
            refs={s['ref']:s['repoURL'] for s in doc['spec']['sources'] if 'ref' in s}
            for filename in source['helm']['valueFiles']:
                assert (checkout(refs['values'])/filename.removeprefix('$values/')).is_file()


def test_shared_namespace_has_valid_metadata():
    namespace=yaml.safe_load((APP/'argocd-eks/apps/roboshop/roboshop-ai-namespace.yaml').read_text())
    config=yaml.safe_load((APP/'argocd-eks/apps/roboshop/roboshop-config-configmap.yaml').read_text())
    assert 'LOG_FORMAT' not in namespace['metadata']
    assert config['data']['LOG_FORMAT']=='json'


def test_imperative_guard_refuses_existing_application(tmp_path):
    import os,subprocess
    binary=tmp_path/'kubectl'
    binary.write_text('#!/bin/sh\ncase "$*" in\n *"get crd"*) echo customresourcedefinition/applications.argoproj.io;;\n *) echo application.argoproj.io/grafana;;\nesac\n')
    binary.chmod(0o700)
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH']}
    result=subprocess.run(['bash',str(ROOT/'scripts/lib/check-argocd-ownership.sh'),'mk4','grafana'],env=env,text=True,capture_output=True)
    assert result.returncode==1
    assert 'Update Git and sync in Argo CD' in result.stderr


def test_imperative_guard_allows_unregistered_application(tmp_path):
    import os,subprocess
    binary=tmp_path/'kubectl'
    binary.write_text('#!/bin/sh\ncase "$*" in\n *"get crd"*) echo customresourcedefinition/applications.argoproj.io;;\nesac\n')
    binary.chmod(0o700)
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH']}
    result=subprocess.run(['bash',str(ROOT/'scripts/lib/check-argocd-ownership.sh'),'mk4','grafana'],env=env,text=True,capture_output=True)
    assert result.returncode==0


def test_inventory_excludes_cluster_managed_networking():
    import json
    from repos import CATALOGS
    inventory=[row for directory in CATALOGS for row in json.loads((directory/'workload-inventory.json').read_text())]
    assert not any(namespace=='kube-system' and name in {'coredns','kindnet','kube-proxy'}
                   for kind,namespace,name in inventory)
    # metrics-server and the observability stack are Terraform releases, not Argo CD workloads.
    assert not any(namespace in TERRAFORM_NAMESPACES for kind,namespace,name in inventory)


def test_required_inventory_matches_active_catalog_and_rejects_missing_workloads():
    import json
    import pytest
    from repos import CATALOGS
    expected={tuple(row) for directory in CATALOGS for row in json.loads((directory/'workload-inventory.json').read_text())}
    assert not any(name in ('product-ingestion','shopping-agent') for _,ns,name in expected)
    assert not any(ns=='roboshop-ai' for _,ns,_ in expected)
    module.validate_inventory(expected)
    # Every remaining entry stays mandatory, including chart-generated workloads.
    for workload in expected:
        with pytest.raises(AssertionError, match='Unmanaged workloads'):
            module.validate_inventory(expected-{workload})


def test_external_validation_calls_required_inventory(monkeypatch,tmp_path):
    # Mock Helm rendering, not the coverage assertion. A required workload no Application renders must fail.
    import json,shutil
    shutil.copy(MINIKUBE/'deploy/minikube/applications/project-mk-platform.yaml',tmp_path)
    (tmp_path/'kustomization.yaml').write_text('resources:\n- project-mk-platform.yaml\n')
    (tmp_path/'workload-inventory.json').write_text(json.dumps([['Deployment','jithendar','missing']]))
    monkeypatch.setattr(module,'CATALOGS',(tmp_path,))
    original=module.render
    monkeypatch.setattr(module,'render',lambda app,external=False: original(app,False))
    import pytest
    with pytest.raises(AssertionError,match='Unmanaged workloads'):
        module.validate(external=True)


def test_frontend_and_nginx_are_retired_into_web():
    import yaml
    resources=yaml.safe_load((ARGOCD/'kustomization.yaml').read_text())['resources']
    assert 'web.yaml' in resources and not {'frontend.yaml','nginx.yaml'} & set(resources)
    assert (APP/'services/web').is_dir()
    assert not (APP/'services/frontend').exists() and not (APP/'services/nginx').exists()


def test_retired_ingestion_has_no_deployable_or_runtime_references():
    assert not (APP/'services/product-ingestion').exists()
    assert not (ROOT/'deploy/minikube/gitops/adoption/product-ingestion').exists()
    assert not (ARGOCD/'product-ingestion.yaml').exists()
    roots=[APP/'services', APP/'.github/workflows', ROOT/'scripts', ARGOCD, ROOT/'minikube-infrastructure', ROOT/'deploy/minikube']
    for directory in roots:
        assert directory.is_dir(),directory
        for path in directory.rglob('*'):
            if not path.is_file() or path.suffix not in ('.py','.mjs','.sh','.yaml','.yml','.tf'):
                continue
            if any(part in ('node_modules','.venv','__pycache__','.terraform','build') for part in path.parts):
                continue
            text=path.read_text()
            assert 'product-ingestion' not in text,path
            assert 'INGESTION_URL' not in text,path


def test_addons_are_terraform_releases_not_applications():
    import re
    from repos import MONITORING_ARGOCD
    tools=yaml.safe_load((MONITORING_ARGOCD/'kustomization.yaml').read_text())['resources']
    shop=yaml.safe_load((ARGOCD/'kustomization.yaml').read_text())['resources']
    assert tools==['project-mk-platform.yaml']
    for name in ADDONS:
        assert not (MONITORING_ARGOCD/f'{name}.yaml').exists(),name
        assert f'{name}.yaml' not in shop,name
    assert not (MONITORING_ARGOCD/'project-observability.yaml').exists()
    releases=set(re.findall(r'^resource "helm_release" "\w+" \{\n\s+name\s+= "([\w-]+)"',
        ''.join(p.read_text() for p in (MINIKUBE/'minikube-infrastructure').glob('*.tf')),re.M))
    assert set(ADDONS)|{'istio-base','istiod','kiali','letsencrypt-jithendar','grafana-dashboards'}<=releases
    # Argo CD may no longer deploy into the namespaces Terraform owns.
    project=yaml.safe_load((MONITORING_ARGOCD/'project-mk-platform.yaml').read_text())['spec']
    assert not {d['namespace'] for d in project['destinations']}&TERRAFORM_NAMESPACES


def test_deploy_catalog_holds_only_mk4_apps():
    import importlib.util as util
    spec=util.spec_from_file_location('catalog_deploy',ROOT/'scripts/argocd/deploy.py')
    deploy=util.module_from_spec(spec);spec.loader.exec_module(deploy)
    apps,projects=deploy.catalog()
    assert apps=={}
    assert set(projects)=={'mk-platform'}
