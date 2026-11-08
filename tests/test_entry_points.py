"""The repository-root wrappers run from any directory."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('script', ['deploy-minikube.sh', 'bootstrap-cluster.sh', 'deploy-argocd.sh', 'create-secrets.sh'])
def test_help_from_another_directory(script):
    result = subprocess.run(['bash', str(ROOT / script), '--help'], cwd='/tmp', capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
