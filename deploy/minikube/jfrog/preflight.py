"""Read-only capacity and DNS checks for this single-host Minikube installation."""

# Portable operator command; imports remain usable by isolated tests.
if __name__ == '__main__':
    import sys as _host_sys
    if not any(arg in ('--help', '-h') for arg in _host_sys.argv[1:]):
        import subprocess as _host_subprocess
        from pathlib import Path as _HostPath
        _host_gate = _HostPath(__file__).resolve().parent / '../../..' / 'scripts/lib/require-centos.sh'
        if _host_subprocess.call(['bash', str(_host_gate)]):
            raise SystemExit(1)

import argparse
import json
import socket
import subprocess


def capacity(nodes, size='standard'):
    minimum_cpu, minimum_ram = (2, 4) if size == 'minimal' else (6, 12)
    for node in nodes['items']:
        alloc = node['status']['allocatable']
        cpu = alloc['cpu']
        cores = float(cpu[:-1]) / 1000 if cpu.endswith('m') else float(cpu)
        memory = alloc['memory']
        factor = next((x for x in ('Ki', 'Mi', 'Gi') if memory.endswith(x)), '')
        ram = float(memory[:-len(factor)] if factor else memory) * {'Ki': 1024, 'Mi': 1024**2, 'Gi': 1024**3, '': 1}[factor]
        if cores >= minimum_cpu and ram >= minimum_ram * 1024**3 and not node.get('spec', {}).get('unschedulable'):
            return
    if size == 'minimal':
        raise RuntimeError('Minimal JFrog requires a schedulable node with at least 2 CPUs and 4 GiB allocatable RAM. No resources changed.')
    raise RuntimeError('JFrog needs a larger Minikube node: at least 6 allocatable CPUs and 12 GiB RAM for this configuration. Upgrade the shared host (recommend 8 vCPU/16 GB or more) and its Minikube node allocation first. No resources changed.')


def check(context, size='standard'):
    command = ['kubectl', '--context=' + context, '--request-timeout=15s']
    capacity(json.loads(subprocess.check_output(command + ['get', 'nodes', '-o', 'json'], text=True)), size)
    try:
        socket.getaddrinfo('jfrog.jithendar.com', 443)
    except socket.gaierror as error:
        raise RuntimeError('Create DNS for jfrog.jithendar.com pointing to the Vultr ingress host first. No resources changed.') from error
    print('Capacity baseline and public DNS checks passed. All Minikube nodes share the physical host; their RAM must not be summed as independent capacity.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--size', choices=['standard','minimal'], default='standard')
    try:
        args = parser.parse_args()
        check(args.context, args.size)
    except RuntimeError as error:
        raise SystemExit(str(error))
