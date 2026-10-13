"""Run trusted repository tests and frozen legacy prepares with recorded runtimes."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from portable.bundle import dependency_payload, encode, no_links, strict_json

LEGACY_FILES = ('d1-prepare/results.json', 'd1-prepare/semantic-requests.json', 'd1-prepare/summary.json',
                'robustness-prepare/results.json', 'robustness-prepare/requests.json', 'robustness-prepare/summary.json')
PROTECTED_FOLDERS = ('runs', 'samples', 'tools', 'engine', 'schemas', 'deliveries', 'okb')
GUARD = """import sys
OKB_OFFLINE_VERIFY_GUARD_ACTIVE = True
def offline(event, arguments):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise RuntimeError('maintainer verification forbids Python network')
sys.addaudithook(offline)
"""
PROBE = """from pathlib import Path
import sitecustomize, sys
assert Path(sitecustomize.__file__).resolve() == Path(sys.argv[1]).resolve()
assert sitecustomize.OKB_OFFLINE_VERIFY_GUARD_ACTIVE is True
for event in ('socket.connect', 'socket.getaddrinfo'):
    try:
        sys.audit(event, None)
    except RuntimeError:
        continue
    raise AssertionError('network audit event not blocked')
print('guard active; audited connect and DNS events blocked without dispatch')
"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with path.open('xb') as stream:
        stream.write(encode(value))


@dataclass(frozen=True)
class Inputs:
    source_root: Path
    dependency_dir: Path
    node_executable: Path
    legacy_paper: Path
    legacy_plan: Path
    legacy_run: Path
    legacy_baseline: Path
    semantic_corpus: Path
    out: Path
    timeout: int = 300


def discovery(root):
    python = []
    javascript = []
    for folder in ('tools/tests', 'engine/tests'):
        directory = root / folder
        scripts = sorted(directory.glob('test_*.py'))
        if not directory.is_dir() or not scripts:
            raise ValueError('full verifier requires both repository test trees')
        python.extend(scripts)
        javascript.extend(sorted(directory.glob('test_*.cjs')))
    if not javascript:
        raise ValueError('full verifier requires the JavaScript suite')
    python.sort(key=lambda path: (path.name != 'test_four_layer.py', path.relative_to(root).as_posix()))
    return python, javascript


def baseline(path):
    document = strict_json(path.read_text(encoding='utf8'))
    records = document.get('legacy_compatibility') if isinstance(document, dict) else None
    if not isinstance(records, list) or len(records) != len(LEGACY_FILES):
        raise ValueError('exactly six frozen legacy hashes are required')
    result = {}
    for record in records:
        if not isinstance(record, dict) or record.get('file') not in LEGACY_FILES or record['file'] in result:
            raise ValueError('invalid or duplicate legacy baseline file')
        digest = record.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid legacy baseline digest')
        result[record['file']] = digest
    return result


def snapshot(root, extra_files=(), *, require_extra=True):
    result = {}
    for name in PROTECTED_FOLDERS:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob('*')):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            no_links(path, directory)
            if path.is_file():
                result[str(path)] = sha(path)
    for path in sorted((root / 'notes').glob('*preregistration*')):
        if path.is_file():
            result[str(path)] = sha(path)
    for path in extra_files:
        if not path.is_file():
            if require_extra:
                raise ValueError('explicit protected file missing')
            continue
        result[str(path)] = sha(path)
    return result


def differences(before, after):
    return [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]


def valid_prepare(directory):
    try:
        marker = strict_json((directory / 'complete.json').read_text(encoding='utf8'))
        if not isinstance(marker, dict) or set(marker) != {'summary_sha256', 'results_sha256'}:
            return False
        for name in ('summary', 'results'):
            path = directory / (name + '.json')
            strict_json(path.read_text(encoding='utf8'))
            if marker[name + '_sha256'] != sha(path):
                return False
        return True
    except (ValueError, OSError, KeyError, TypeError):
        return False


def environment(guard, dependencies, root):
    result = dict(os.environ)
    for name in list(result):
        if name.endswith(('_API_KEY', '_TOKEN')) or name.startswith('OKB_FACT_JUDGE'):
            result.pop(name)
    result.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                  PYTHONPATH=os.pathsep.join(map(str, (guard, dependencies, root, root / 'tools'))))
    return result


def run_command(label, command, *, out, root, env, timeout, expected=0):
    log = out / (label.replace('/', '-').replace('.py', '') + '.log')
    started = time.monotonic()
    with log.open('xb') as stream:
        try:
            process = subprocess.run(command, cwd=root, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 'timeout'
        except OSError:
            code = 'launch-error'
    return dict(test=label, exit=code, expected_exit=expected, passed=code == expected,
                seconds=round(time.monotonic() - started, 2), log=str(log), log_sha256=sha(log))


def prepare_inputs(inputs):
    if type(inputs.timeout) is not int or inputs.timeout < 1:
        raise ValueError('positive per-process timeout required')
    root, out = inputs.source_root, inputs.out
    if not root.is_dir() or not inputs.dependency_dir.is_dir() or not inputs.legacy_run.is_dir():
        raise ValueError('missing explicit source, dependency or historical run directory')
    files = (inputs.node_executable, inputs.legacy_paper, inputs.legacy_plan,
             inputs.legacy_baseline, inputs.semantic_corpus, root / 'tools/score_four_layer.py',
             root / 'tools/semantic_robustness.py', root / 'pyproject.toml', root / 'okb_portable.py')
    if any(not path.is_file() for path in files):
        raise ValueError('missing explicit historical or runtime material')
    if out.exists() or not out.parent.is_dir():
        raise ValueError('output must be new with an existing parent')
    for directory in (root, inputs.dependency_dir, inputs.legacy_run):
        if out.is_relative_to(directory) or directory.is_relative_to(out):
            raise ValueError('output overlaps protected input directory')
    if any(path.is_relative_to(out) for path in files):
        raise ValueError('output overlaps protected input file')
    scripts, javascript = discovery(root)
    expected = baseline(inputs.legacy_baseline)
    payload, locked = dependency_payload(inputs.dependency_dir)
    dependency_hashes = {str(inputs.dependency_dir / name.removeprefix('vendor/')): hashlib.sha256(content).hexdigest()
                         for name, content in payload.items()}
    extra = list(files)
    protected = snapshot(root, extra)
    for path in sorted(inputs.legacy_run.rglob('*')):
        no_links(path, inputs.legacy_run)
        if path.is_file():
            protected[str(path)] = sha(path)
    protected[str(Path(sys.executable).resolve())] = sha(sys.executable)
    return scripts, javascript, expected, locked, dependency_hashes, extra, protected


def verify(inputs):
    scripts, javascript, expected, locked, dependency_hashes, extra, protected = prepare_inputs(inputs)
    root, out = inputs.source_root, inputs.out
    out.mkdir()
    guard = out / 'network-guard'
    guard.mkdir()
    with (guard / 'sitecustomize.py').open('x', encoding='utf8') as stream:
        stream.write(GUARD)
    env = environment(guard, inputs.dependency_dir, root)
    write(out / 'protected-before.json', protected)
    write(out / 'dependencies-before.json', dependency_hashes)
    write(out / 'input-contract.json', dict(format='okb-offline-maintainer-input-v1-draft',
        source_root=str(root), historical_inputs={name: str(getattr(inputs, name)) for name in
            ('legacy_paper', 'legacy_plan', 'legacy_run', 'legacy_baseline', 'semantic_corpus')},
        dependencies=locked, python=dict(executable=sys.executable, version=sys.version, sha256=sha(sys.executable)),
        node=dict(executable=str(inputs.node_executable), sha256=sha(inputs.node_executable)),
        python_tests=[path.relative_to(root).as_posix() for path in scripts],
        javascript_tests=[path.relative_to(root).as_posix() for path in javascript],
        expected_legacy_hashes=expected, semantic_reliability_validated=False))
    results = []
    def run(label, command, expected_exit=0):
        result = run_command(label, command, out=out, root=root, env=env,
                             timeout=inputs.timeout, expected=expected_exit)
        results.append(result)
        (out / 'progress.json').write_bytes(encode(results))
        print(label, result['exit'], flush=True)
        return result
    probe = run('guard-preflight', [sys.executable, '-X', 'utf8', '-c', PROBE, str(guard / 'sitecustomize.py')])
    if not probe['passed']:
        raise ValueError('offline guard preflight failed; tests not dispatched')
    node = run('node-version', [str(inputs.node_executable), '--version'])
    version = Path(node['log']).read_text(encoding='utf8').strip()
    if not node['passed'] or not re.fullmatch(r'v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
        raise ValueError('explicit Node runtime did not return a Node version; tests not dispatched')
    for script in scripts:
        run(script.relative_to(root).as_posix(), [sys.executable, '-X', 'utf8', str(script)])
    for script in javascript:
        run(script.relative_to(root).as_posix(), [str(inputs.node_executable), str(script)])
    run('d1-prepare', [sys.executable, '-X', 'utf8', str(root / 'tools/score_four_layer.py'),
        '--paper', str(inputs.legacy_paper), '--plan', str(inputs.legacy_plan), '--run-dir', str(inputs.legacy_run),
        '--out-dir', str(out / 'd1-prepare')], 1)
    run('robustness-prepare', [sys.executable, '-X', 'utf8', str(root / 'tools/semantic_robustness.py'),
        '--corpus', str(inputs.semantic_corpus), '--out-dir', str(out / 'robustness-prepare')], 1)
    after = snapshot(root, extra, require_extra=False)
    for path in sorted(inputs.legacy_run.rglob('*')):
        no_links(path, inputs.legacy_run)
        if path.is_file():
            after[str(path)] = sha(path)
    after[str(Path(sys.executable).resolve())] = sha(sys.executable)
    drift = differences(protected, after)
    dependency_drift = [name for name, digest in dependency_hashes.items() if not Path(name).is_file() or sha(name) != digest]
    legacy = []
    for name, digest in expected.items():
        candidate = out / name
        actual = sha(candidate) if candidate.is_file() else None
        legacy.append(dict(file=name, expected_sha256=digest, actual_sha256=actual, byte_identical=actual == digest))
    markers = all(valid_prepare(out / folder) for folder in ('d1-prepare', 'robustness-prepare'))
    summary = dict(kind='offline-maintainer-full-verification-not-independent-review',
        python_suites=len(scripts), javascript_suites=len(javascript), results=results,
        protected_count=len(protected), protected_unchanged=not drift, changed_protected_files=drift,
        dependency_files=len(dependency_hashes), dependencies_unchanged=not dependency_drift,
        changed_dependency_files=dependency_drift, node_version=version, guard_preflight_passed=True,
        prepare_outputs_valid=markers, legacy_compatibility=legacy,
        legacy_byte_compatibility_verified=all(row['byte_identical'] for row in legacy),
        semantic_reliability_validated=False, real_judge_http_calls=None,
        judge_http_calls_measured=False, verifier_dispatches_judge_http=False, fresh_openkylin_accepted=False,
        network_policy='Python connect/DNS audit events blocked and inherited; trusted Node tests are not OS-network-sandboxed',
        all_expected_exits=all(row['passed'] for row in results) and markers)
    summary['passed'] = summary['all_expected_exits'] and not drift and not dependency_drift and summary['legacy_byte_compatibility_verified']
    write(out / 'summary.json', summary)
    members = {path.relative_to(out).as_posix(): sha(path) for path in sorted(out.rglob('*')) if path.is_file()}
    write(out / 'complete.json', dict(format='okb-offline-maintainer-complete-v1-draft', passed=summary['passed'], files=members))
    return summary
