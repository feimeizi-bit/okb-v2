"""Offline-only launch policy and dependency/ABI checks for candidate bundles."""
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys

from portable.bundle import DEPENDENCIES, canonical_name, runtime_abi, verify

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {'validate', 'simulate', 'score-vnext', 'report-vnext', 'report-multirun',
            'review-judge-cost', 'annotate-semantic', 'review-semantic-split', 'kappa'}
DENIED_FLAGS = {'--live', '--authorize-paid-calls', '--api-key-env'}


def offline(event, arguments):
    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
        raise RuntimeError('portable offline entry forbids network and child processes')


def dependencies(root, manifest):
    expected = manifest.get('runtime_abi')
    actual = runtime_abi()
    if not isinstance(expected, dict) or any(expected.get(field) != actual[field] for field in ('system', 'machine', 'implementation', 'cache_tag')):
        raise ValueError('bundle native dependency ABI does not match this interpreter')
    declared = manifest.get('dependencies')
    if not isinstance(declared, list) or {canonical_name(item['name']) for item in declared} != set(DEPENDENCIES) or len(declared) != len(DEPENDENCIES):
        raise ValueError('invalid dependency lock')
    vendor = root / 'vendor'
    installed = {canonical_name(item.metadata['Name']): item for item in metadata.distributions(path=[str(vendor)])}
    for item in declared:
        name = canonical_name(item['name'])
        if name not in installed or installed[name].version != item['version'] or item.get('module') != DEPENDENCIES[name]:
            raise ValueError('vendored dependency version differs from lock')
        module = importlib.import_module(item['module'])
        if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(vendor.resolve()):
            raise ValueError('dependency was resolved outside this bundle')


def dispatch(root, arguments):
    sys.dont_write_bytecode = True
    sys.addaudithook(offline)
    if any(argument.split('=', 1)[0].startswith('--') and len(argument.split('=', 1)[0]) > 2
           and any(flag.startswith(argument.split('=', 1)[0]) for flag in DENIED_FLAGS)
           for argument in arguments):
        raise ValueError('paid or credential flags are not accepted by offline entry')
    for name in list(os.environ):
        if name.endswith(('_API_KEY', '_TOKEN')) or name.startswith(('OKB_FACT_JUDGE', 'DASHSCOPE_API_KEY')):
            os.environ.pop(name, None)
    manifest = verify(root)
    sys.path[:0] = [str(root), str(root / 'tools'), str(root / 'vendor')]
    dependencies(root, manifest)
    if not arguments or arguments == ['--check']:
        print(json.dumps(dict(bundle_verified=True, dependency_versions_verified=True,
                              runtime_abi=runtime_abi(), files=len(manifest['files']),
                              network_policy='Python audit guard; not an OS sandbox',
                              accuracy_validated=False, fresh_openkylin_accepted=False)))
        return 0
    if arguments[0] in ('--help', '-h'):
        print('python okb_portable.py --check | --selfcheck --out-dir NEW_DIRECTORY | OFFLINE_COMMAND [arguments]')
        print('Offline commands: ' + ', '.join(sorted(COMMANDS)))
        return 0
    if arguments[0] == '--selfcheck':
        from portable.selfcheck import main as selfcheck
        return selfcheck(arguments[1:], root=root)
    if arguments[0] not in COMMANDS:
        raise ValueError('command is outside offline candidate scope')
    from okb.cli import main as cli
    return cli(arguments)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return dispatch(ROOT, arguments)
    except (ValueError, OSError, KeyError, TypeError, ImportError, RuntimeError) as error:
        print('Portable entry rejected: ' + type(error).__name__, file=sys.stderr)
        return 2
