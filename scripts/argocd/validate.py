#!/usr/bin/env python3
"""Render the Argo CD catalog and detect overlapping ownership before adoption."""
import argparse
import fnmatch
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from repos import CATALOGS, OPTIONAL_ARGOCD, ROOT, checkout  # noqa: E402


def command(args):
    return subprocess.check_output(args, text=True, cwd=ROOT)


def objects(text):
    return [d for d in yaml.safe_load_all(text) if d]


def catalog():
    """Every catalog rendered together, so ownership and project checks span both repositories.
    Opt-in Applications (applications/optional) are checked too; only their workloads are not required."""
    return [doc for directory in (*CATALOGS, OPTIONAL_ARGOCD) for doc in objects(command(['kubectl', 'kustomize', str(directory)]))]


def render(app, external=False):
    result=[]
    spec=app['spec']
    for source in spec.get('sources', [spec.get('source')]):
        if not source:continue
        if 'ref' in source:continue
        if 'chart' in source:
            if not external:continue
            helm=source.get('helm', {})
            url=source['repoURL']
            args=['helm','template',helm.get('releaseName',app['metadata']['name'])]
            if url.startswith('https://'):
                args += [source['chart'],'--repo',url]
            else:
                args += ['oci://'+url+'/'+source['chart']]
            args += ['--version',source['targetRevision'],'--namespace',spec['destination']['namespace'],'--include-crds']
            refs={s['ref']:s['repoURL'] for s in spec.get('sources',[]) if 'ref' in s}
            for value in helm.get('valueFiles',[]):
                assert value.startswith('$values/'),value
                args += ['-f',str(checkout(refs['values'])/value.removeprefix('$values/'))]
            result += objects(command(args));continue
        path=checkout(source['repoURL'])/source['path']
        assert path.is_dir(),path
        if (path/'Chart.yaml').exists():
            helm=source.get('helm',{})
            args=['helm','template',helm.get('releaseName',app['metadata']['name']),str(path),'--namespace',spec['destination']['namespace']]
            with tempfile.NamedTemporaryFile(mode='w',suffix='.yaml') as values:
                yaml.safe_dump(helm.get('valuesObject',{}),values);values.flush()
                result += objects(command(args+['-f',values.name]))
        elif (path/'kustomization.yaml').exists():
            result += objects(command(['kubectl','kustomize',str(path)]))
        else:
            pattern=source['directory']['include']
            patterns=pattern.strip('{}').split(',')
            files=[p for p in path.iterdir() if p.is_file() and any(fnmatch.fnmatch(p.name,pat) for pat in patterns)]
            assert files,(path,pattern)
            for file in sorted(files):result+=objects(file.read_text())
    return result


def validate_inventory(workloads):
    # This is the required active catalog, not a historical snapshot of live pods.
    # Do not filter expected entries by rendered apps: that would hide omissions.
    expected = {tuple(row) for directory in CATALOGS for row in json.loads((directory/'workload-inventory.json').read_text())}
    assert expected <= workloads, f'Unmanaged workloads: {sorted(expected-workloads)}'


def validate(external=False):
    docs=catalog();projects={d['metadata']['name']:d for d in docs if d['kind']=='AppProject'}
    apps=[d for d in docs if d['kind']=='Application']
    kinds_names=[(d['kind'],d['metadata']['name']) for d in docs]
    assert len(kinds_names)==len(set(kinds_names)),f'Defined in more than one catalog: {sorted({k for k in kinds_names if kinds_names.count(k)>1})}'
    owners={};workloads=set()
    assert len(apps)==len({d['metadata']['name'] for d in apps})
    for app in apps:
        name=app['metadata']['name'];spec=app['spec']
        assert name not in ('argocd','traefik','coredns','kindnet','kube-proxy')
        assert not app['metadata'].get('finalizers'),name
        assert spec['project'] in projects
        project=projects[spec['project']]['spec']
        assert spec['destination']['namespace'] not in ('argocd','traefik')
        assert 'automated' not in spec.get('syncPolicy',{}),name
        assert 'FailOnSharedResource=true' in spec['syncPolicy']['syncOptions']
        for resource in render(app,external):
            kind=resource['kind'];meta=resource['metadata'];namespace=meta.get('namespace','')
            cluster_kind=kind in ('Namespace','ClusterRole','ClusterRoleBinding','CustomResourceDefinition','APIService','ClusterIssuer','MutatingWebhookConfiguration','ValidatingWebhookConfiguration')
            if not namespace and not cluster_kind:namespace=spec['destination']['namespace']
            assert namespace not in ('argocd','traefik'),(name,kind,meta['name'])
            if kind=='Secret' and name=='harbor':
                # The chart always renders these; with every credential in harbor-credentials they hold no values.
                assert meta['name'] in ('harbor-core','harbor-jobservice','harbor-registry','harbor-registryctl'),meta['name']
                assert not resource.get('stringData') and not any((resource.get('data') or {}).values()),f'harbor: Secret/{meta["name"]} carries a value: move it to harbor-credentials'
            elif kind=='Secret':
                assert name=='jfrog' and meta['name']=='jfrog-artifactory-unified-secret',f'{name} renders a credential Secret: provision credentials externally'
                assert set(resource.get('stringData',{}))=={'access.config.patch.yml','binarystore.xml','system.yaml'}
                assert not resource.get('data')
            key=(resource['apiVersion'].split('/')[0] if '/' in resource['apiVersion'] else '',kind,namespace,meta['name'])
            if namespace:
                assert any(d['namespace']==namespace for d in project['destinations']),(name,namespace)
            else:
                assert any(rule['kind'] in ('*',kind) and rule['group'] in ('*',key[0]) for rule in project['clusterResourceWhitelist']),(name,key)
            assert key not in owners,f'{key} belongs to both {owners.get(key)} and {name}'
            owners[key]=name
            if kind in ('Deployment','StatefulSet','DaemonSet'):workloads.add((kind,namespace,meta['name']))
            # A prune sync once deleted Namespace/roboshop-ai and every volume in it.
            if kind in ('Namespace','PersistentVolumeClaim'):
                options=(meta.get('annotations') or {}).get('argocd.argoproj.io/sync-options','')
                assert 'Prune=false' in options and 'Delete=false' in options,f'{name}: {kind}/{meta["name"]} needs argocd.argoproj.io/sync-options: Prune=false,Delete=false'
            # Minikube hostPath volumes exist only on the node that first ran the pod.
            if kind in ('Deployment','StatefulSet'):
                pod=resource['spec']['template']['spec']
                if resource['spec'].get('volumeClaimTemplates') or any('persistentVolumeClaim' in v for v in pod.get('volumes',[])):
                    assert (pod.get('nodeSelector') or {}).get('kubernetes.io/hostname'),f'{name}: {kind}/{meta["name"]} mounts a hostPath-backed PVC; pin it with nodeSelector kubernetes.io/hostname'
    if external:
        validate_inventory(workloads)
    print(f'Validated {len(apps)} Applications, {len(owners)} unique resources; external charts {"included" if external else "not rendered"}.')
    return apps,owners


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external-charts',action='store_true')
    args=parser.parse_args();validate(args.external_charts)
