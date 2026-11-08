"""Active manifests are one YAML document/resource per source file."""
from pathlib import Path
import re
import subprocess
import yaml

ROOT=Path(__file__).resolve().parents[1]


def active_yaml():
    # deploy/eks and roboshop-ai are checked by the tools repository's copy of this test.
    bases=[ROOT/'deploy/minikube',ROOT/'.github']
    for base in bases:
        assert base.is_dir(),base
        for p in base.rglob('*'):
            if p.suffix not in ('.yaml','.yml') or any(x in p.parts for x in ('node_modules','.venv','__pycache__')):
                continue
            yield p


def test_each_active_yaml_is_one_document():
    for path in active_yaml():
        source=path.read_text()
        assert not re.search(r'^---\s*$',source,re.M),str(path)
        if 'templates' not in path.parts:
            docs=list(yaml.safe_load_all(source))
            assert len(docs)==1,str(path)
            assert not (isinstance(docs[0],dict) and docs[0].get('kind')=='List'),str(path)


def test_each_local_chart_template_renders_at_most_one_resource():
    charts=('prometheus','loki','jaeger','otel-collector','grafana/dashboards')
    for chart in ('deploy/minikube/cert-manager/issuers',*(f'deploy/minikube/observability/{c}' for c in charts)):
        output=subprocess.check_output(['helm','template','layout',str(ROOT/chart),'-n','layout'],text=True)
        sources=re.findall(r'^# Source: (.+)$',output,re.M)
        assert len(sources)==len(set(sources)),chart


def test_local_kustomize_references_exist():
    for base in (ROOT/'deploy/minikube',):
        for path in base.rglob('kustomization.yaml'):
            if any(x in path.parts for x in ('node_modules','.venv')):continue
            for resource in yaml.safe_load(path.read_text()).get('resources',[]):
                if '://' not in resource:
                    assert (path.parent/resource).exists(),(path,resource)
