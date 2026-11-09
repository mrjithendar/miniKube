"""No image tag pins a single CPU architecture: mk4's nodes are arm64 and every image must be a multi-arch tag.

Dependabot bumped otel/opentelemetry-collector from 0.162.0 to 0.164.0-386 (the 32-bit x86 build) on 2026-11-09; the
collector crash-looped with "exec format error" and the Terraform apply timed out on helm_release.otel_collector.
"""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
ARCH_SUFFIX=re.compile(r'-(386|amd64|arm|arm64|armv6|armv7|ppc64le|s390x|riscv64)$')


def test_no_image_tag_has_an_architecture_suffix():
    bad=[]
    for path in (ROOT/'deploy/minikube').rglob('*.y*ml'):
        for line in path.read_text().splitlines():
            m=re.match(r'^\s*-?\s*image:\s*"?([^"\s{]+)"?\s*$',line)
            if m and ':' in m.group(1).rsplit('/',1)[-1] and ARCH_SUFFIX.search(m.group(1)):
                bad.append(f'{path.relative_to(ROOT)}: {m.group(1)}')
    assert not bad,bad
