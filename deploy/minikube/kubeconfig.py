#!/usr/bin/env python3
"""Convert a remote Minikube export into a direct-access kubeconfig.

kubeconfig.sh handles SSH, private temporary files, connection checks and the
final file replacement. This helper only validates and transforms JSON.
"""

# Portable operator command; imports remain usable by isolated tests.
if __name__ == '__main__':
    import sys as _host_sys
    if not any(arg in ('--help', '-h') for arg in _host_sys.argv[1:]):
        import subprocess as _host_subprocess
        from pathlib import Path as _HostPath
        _host_gate = _HostPath(__file__).resolve().parent / '../..' / 'scripts/lib/require-centos.sh'
        if _host_subprocess.call(['bash', str(_host_gate)]):
            raise SystemExit(1)


import argparse
import json
import re
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def prepare_config(export: dict, host: str) -> dict:
    """Update the connection address while retaining the trusted API identity."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise ValueError(
            "API_HOST must be a hostname or IPv4 address, without scheme, port or path."
        )

    port = int(export["port"])
    if not 1 <= port <= 65535:
        raise ValueError("Invalid remote API port.")

    config = export["config"]
    if len(config.get("clusters", [])) != 1 or not config.get("current-context"):
        raise ValueError("Remote export does not contain one selected cluster.")

    cluster = config["clusters"][0]["cluster"]
    original_host = urlsplit(cluster["server"]).hostname
    if not original_host or not cluster.get("certificate-authority-data"):
        raise ValueError(
            "Remote export is missing the API hostname or embedded CA certificate."
        )

    # The transport uses the public domain; certificate validation still uses
    # the original API identity, including IP-based certificate SANs.
    cluster.setdefault("tls-server-name", original_host)
    cluster["server"] = f"https://{host}:{port}"
    cluster["insecure-skip-tls-verify"] = False
    cluster.pop("proxy-url", None)
    return config


def merge_config(source: Path, destination: Path) -> None:
    """Refresh named Minikube entries, retaining unrelated contexts and credentials."""
    incoming = json.loads(source.read_text())
    selected = incoming.get("current-context")
    if not selected or not any(c.get("name") == selected for c in incoming.get("contexts", [])):
        raise ValueError("Imported kubeconfig has no valid selected context.")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if destination.exists():
        # Flatten resolves relative certificate paths before rewriting the file.
        result = subprocess.run(
            ["kubectl", "--kubeconfig=" + str(destination), "config", "view",
             "--raw", "--flatten", "-o", "json"],
            capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError("Cannot read existing kubeconfig; it was not changed.")
        existing = json.loads(result.stdout)
    merged = dict(existing)
    merged.update({"apiVersion": "v1", "kind": "Config", "current-context": selected})
    for key in ("clusters", "users", "contexts"):
        entries = {entry["name"]: entry for entry in existing.get(key, []) or []}
        for entry in incoming.get(key, []):
            entries[entry["name"]] = entry
        merged[key] = list(entries.values())
    backup = None
    if destination.exists():
        fd, name = tempfile.mkstemp(prefix=destination.name + ".backup-", dir=destination.parent)
        os.close(fd)
        backup = Path(name)
        shutil.copyfile(destination, backup)
    fd, name = tempfile.mkstemp(prefix=".kubeconfig-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(merged, output, indent=2)
            output.write("\n")
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    if backup:
        print(f"Previous kubeconfig backed up: {backup}")
    print(f"Imported context {selected} into {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merge", action="store_true", help="Merge prepared JSON into the default config")
    parser.add_argument("source", type=Path, help="Remote JSON containing port and config")
    parser.add_argument("destination", type=Path, help="Temporary kubeconfig output path")
    parser.add_argument("host", nargs="?", help="Public hostname or IPv4 address")
    args = parser.parse_args()

    try:
        if args.merge:
            merge_config(args.source, args.destination)
            return
        if not args.host:
            raise ValueError("Public hostname is required.")
        with args.source.open() as source:
            config = prepare_config(json.load(source), args.host)
        # This file contains client credentials, so keep it private even when
        # the helper is invoked independently of the shell script.
        with args.destination.open("w") as destination:
            args.destination.chmod(0o600)
            json.dump(config, destination, indent=2)
            destination.write("\n")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(1, f"ERROR: Could not prepare kubeconfig: {error}\n")

    print(f"Checking direct API: {config['clusters'][0]['cluster']['server']}")


if __name__ == "__main__":
    main()
