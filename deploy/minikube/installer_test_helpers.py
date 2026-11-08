"""Keep copied installer tests offline while exercising the real Argo ownership guard."""
from pathlib import Path
import shlex
ROOT=Path(__file__).resolve().parents[2]


def prepare_source(source):
    # Host policy has its own test suite.
    source='\n'.join('roboshop_require_centos() { return 0; }' if line.startswith('source ') and 'require-centos.sh' in line else line for line in source.splitlines())+'\n'
    source=source.replace('"$SCRIPT_DIR/../../scripts/lib/check-argocd-ownership.sh"',shlex.quote(str(ROOT/'scripts/lib/check-argocd-ownership.sh')))
    return source
