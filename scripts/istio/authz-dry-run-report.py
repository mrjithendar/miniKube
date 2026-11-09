#!/usr/bin/env python3
"""What the dry-run AuthorizationPolicies would have refused: one line per pod with a non-zero count.

  python3 scripts/istio/authz-dry-run-report.py [--context mk4] [namespace ...]

An AuthorizationPolicy annotated istio.io/dry-run: "true" lets every request through and only counts, in its pod's
sidecar, what it would have denied (Envoy stats ending in rbac.istio_dry_run_allow_shadow_denied, HTTP and TCP). This
reads those counters from every meshed pod in the namespaces that have such a policy (or the ones given). Counters
start at 0 when a pod starts, so run it after a day of normal traffic. All zero: set dryRun false (miniKube
deploy/minikube/istio/mesh/values.yaml, and the applications' own switches). A non-zero count names the pod that was
called; the caller is in that pod's sidecar debug log (`istioctl proxy-config log <pod> --level rbac:debug`).
Read-only: it never changes the cluster.
"""
import argparse
import json
import subprocess
import sys


def kubectl(context, *args):
    result = subprocess.run(['kubectl', '--context', context, *args], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f'kubectl {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout


def dry_run_namespaces(context):
    policies = json.loads(kubectl(context, 'get', 'authorizationpolicies.security.istio.io', '-A', '-o', 'json'))
    return sorted({p['metadata']['namespace'] for p in policies['items']
                   if (p['metadata'].get('annotations') or {}).get('istio.io/dry-run') == 'true'})


def shadow_denied(context, namespace, pod):
    stats = subprocess.run(['kubectl', '--context', context, '-n', namespace, 'exec', pod, '-c', 'istio-proxy', '--',
                            'pilot-agent', 'request', 'GET', 'stats'], capture_output=True, text=True)
    if stats.returncode:
        return None
    counts = {}
    for line in stats.stdout.splitlines():
        name, _, value = line.rpartition(': ')
        if name.endswith('istio_dry_run_allow_shadow_denied') and value.strip().isdigit() and int(value):
            counts[name] = int(value)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--context', default='mk4')
    parser.add_argument('namespaces', nargs='*')
    args = parser.parse_args()
    namespaces = args.namespaces or dry_run_namespaces(args.context)
    if not namespaces:
        print('No dry-run AuthorizationPolicy on the cluster.')
        return 0
    denied = 0
    for namespace in namespaces:
        pods = json.loads(kubectl(args.context, '-n', namespace, 'get', 'pods', '-o', 'json'))['items']
        for pod in pods:
            containers = [c['name'] for c in pod['spec'].get('containers', []) + pod['spec'].get('initContainers', [])]
            if pod['status'].get('phase') != 'Running' or 'istio-proxy' not in containers:
                continue
            name = pod['metadata']['name']
            counts = shadow_denied(args.context, namespace, name)
            if counts is None:
                print(f'{namespace}/{name}: could not read the sidecar stats', file=sys.stderr)
                continue
            for stat, value in sorted(counts.items()):
                denied += value
                print(f'{namespace}/{name}: {value} {stat}')
    print(f'{denied} request(s) or connection(s) would have been refused in {", ".join(namespaces)}.')
    return 1 if denied else 0


if __name__ == '__main__':
    sys.exit(main())
