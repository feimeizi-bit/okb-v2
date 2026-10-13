#!/usr/bin/env python3
"""Read-only inventory: never invoke Agent CLIs, read secrets, or reset memory."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil


def inventory(repo):
    repo = Path(repo).resolve()
    release = {}
    p = Path('/etc/os-release')
    if p.is_file():
        for line in p.read_text(encoding='utf8').splitlines():
            key, sep, value = line.partition('=')
            if sep and key in {'ID', 'VERSION_ID', 'PRETTY_NAME'}:
                release[key] = value.strip('"')
    versions = {}
    for name in ('PyYAML', 'jsonschema'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    memory = None
    p = Path('/proc/meminfo')
    if p.is_file():
        for line in p.read_text(encoding='ascii').splitlines():
            if line.startswith('MemAvailable:'):
                memory = int(line.split()[1]) * 1024
    cli = {}
    for name, fallback in [('openclaw', Path.home()/'.local/bin/openclaw'),
                           ('hermes', Path.home()/'.local/bin/hermes')]:
        cli[name] = {'on_path': shutil.which(name) is not None,
                     'common_path_exists': fallback.is_file(), 'version': None}
    return {'format': 'okb-pilot-environment-v1', 'purpose': 'inventory-not-readiness-approval',
            'os': release, 'platform': platform.system(), 'python': platform.python_version(),
            'dependencies': versions, 'agent_cli_presence': cli,
            'repo_structure_present': all((repo/x).is_dir() for x in ('engine', 'tools', 'schemas')),
            'available_memory_bytes': memory, 'repo_disk_free_bytes': shutil.disk_usage(repo).free,
            'not_checked': ['agent versions', 'configured models and pricing', 'embed service health',
                            'profile isolation', 'memory probe correctness', 'API access and billing caps'],
            'agent_calls': 0, 'memory_resets': 0, 'release_authorized': False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument('--out', required=True, help='New JSON file; parent must exist')
    args = ap.parse_args(argv)
    result = inventory(args.repo)
    with Path(args.out).open('x', encoding='utf8', newline='\n') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('Inventory written. No Agent invocation; not permission to run.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
