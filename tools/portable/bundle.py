"""Build and verify a relocatable runtime-file bundle without network or Git."""
from importlib import metadata
import csv
import hashlib
import io
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import sys

FORMAT = 'okb-portable-offline-bundle-v1-draft'
MANIFEST = 'portable-manifest.json'
DEPENDENCIES = {
    'attrs': 'attrs',
    'jsonschema': 'jsonschema',
    'jsonschema-specifications': 'jsonschema_specifications',
    'pyyaml': 'yaml',
    'referencing': 'referencing',
    'rpds-py': 'rpds',
    'typing-extensions': 'typing_extensions',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf8')


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-standard JSON constant')
    return json.loads(data, object_pairs_hook=unique, parse_constant=constant)


def canonical_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def safe_relative(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('invalid bundle path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or relative.as_posix() != name or any(part in ('', '.', '..') for part in name.split('/')):
        raise ValueError('bundle path escapes root')
    return relative


def no_links(path, root):
    path = Path(path)
    root = Path(root)
    if not path.is_relative_to(root):
        raise ValueError('file escapes declared root')
    for candidate in (root, *reversed(path.relative_to(root).parents), path.relative_to(root)):
        checked = candidate if candidate == root else root / candidate
        info = checked.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symbolic links and reparse points are not portable inputs')


def _system_name():
    """Spawn-free system name: platform.system() may fall back to `cmd /c ver`
    (subprocess) on some Windows interpreters, which the portable audit hook
    forbids by design. os.name is spawn-free and yields the same 'Windows'
    label for ABI comparison."""
    return "Windows" if os.name == "nt" else platform.system()


def _machine_name():
    """Spawn-free machine ABI: on Windows platform.machine() may route through
    platform.uname()/win32_ver() and spawn `cmd /c ver`; PROCESSOR_ARCHITECTURE
    is what platform.machine() reports there anyway. POSIX uname is spawn-free."""
    if os.name == "nt":
        return os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()
    return platform.machine()


def runtime_abi():
    return dict(system=_system_name(), machine=_machine_name(),
                implementation=platform.python_implementation(), cache_tag=sys.implementation.cache_tag,
                python_version=platform.python_version())


def dependency_payload(directory):
    directory = Path(directory).absolute()
    no_links(directory, directory)
    found = {}
    for directory_entry in directory.glob('*.dist-info'):
        no_links(directory_entry, directory)
    for distribution in metadata.distributions(path=[str(directory)]):
        name = canonical_name(distribution.metadata['Name'])
        if name not in DEPENDENCIES:
            continue
        if name in found or not distribution.read_text('RECORD'):
            raise ValueError('ambiguous dependency or missing RECORD')
        found[name] = distribution
    if set(found) != set(DEPENDENCIES):
        raise ValueError('dependency directory lacks required runtime distributions')
    payload = {}
    locked = []
    for name in sorted(found):
        distribution = found[name]
        included = []
        excluded = []
        for record in csv.reader(io.StringIO(distribution.read_text('RECORD'))):
            if len(record) != 3:
                raise ValueError('invalid dependency RECORD row')
            relative = record[0].replace('\\', '/')
            if name == 'jsonschema' and relative in ('../../bin/jsonschema', '../../bin/jsonschema.exe'):
                excluded.append(relative)
                continue
            safe_relative(relative)
            record_path = PurePosixPath(relative)
            if '__pycache__' in record_path.parts or record_path.suffix == '.pyc' or record_path.name == 'direct_url.json':
                continue
            source = directory / relative
            no_links(source, directory)
            if not source.is_file():
                raise ValueError('dependency RECORD references a missing file')
            destination = 'vendor/' + relative
            content = source.read_bytes()
            if destination in payload and payload[destination] != content:
                raise ValueError('dependency file collision')
            payload[destination] = content
            included.append(destination)
        locked.append(dict(name=distribution.metadata['Name'], version=distribution.version,
                           module=DEPENDENCIES[name], files=included, excluded_external_scripts=excluded))
    return payload, locked


def source_payload(root):
    root = Path(root).absolute()
    no_links(root, root)
    selected = [root / 'okb_portable.py', root / 'pyproject.toml']
    for folder in ('okb', 'tools', 'engine'):
        for source in sorted((root / folder).rglob('*.py')):
            relative = source.relative_to(root)
            if 'tests' in relative.parts or '__pycache__' in relative.parts:
                continue
            if source.name.startswith('_') and source.name not in ('__init__.py', '__main__.py'):
                continue
            selected.append(source)
    selected.extend(sorted((root / 'schemas').glob('*.json')))
    selected.append(root / 'samples/semantic-robustness.v1.json')
    selected.append(root / 'docs/portable-offline-v1.md')
    payload = {}
    for source in selected:
        no_links(source, root)
        relative = source.relative_to(root).as_posix()
        content = source.read_bytes()
        if source.suffix in ('.py', '.json', '.md', '.toml') and re.search(rb'\bsk-[A-Za-z0-9._-]{20,}\b', content):
            raise ValueError('credential-like material in selected source')
        payload[relative] = content
    payload['README.md'] = payload['docs/portable-offline-v1.md']
    return payload


def build(root, dependency_dir, out):
    root, dependency_dir, out = map(lambda value: Path(value).absolute(), (root, dependency_dir, out))
    if out.exists() or not out.parent.is_dir() or any(out.resolve().is_relative_to(source.resolve()) for source in (root, dependency_dir)):
        raise ValueError('output must be new, outside source and dependencies, with an existing parent')
    sources = source_payload(root)
    dependencies, locked = dependency_payload(dependency_dir)
    payload = {**sources, **dependencies}
    requirements = '\n'.join(f"{item['name']}=={item['version']}" for item in locked) + '\n'
    payload['requirements.portable.lock.txt'] = requirements.encode('utf8')
    manifest = dict(format=FORMAT, files={name: sha(content) for name, content in sorted(payload.items())},
                    dependencies=locked, runtime_abi=runtime_abi(),
                    dependency_mode='copied-runtime-files-not-pip-installation',
                    source_selection='code-schema-and-explicit-public-development-fixtures-only',
                    outcome_version='v2', evidence_policy='scoped-v1',
                    accuracy_validated=False, fresh_openkylin_accepted=False, publication_authorized=False,
                    limits=['Requires a separately supplied compatible Python interpreter.',
                            'Native dependencies bind this bundle to the recorded system, architecture and Python ABI.',
                            'Hashes detect drift, not authorship or malicious modification of the manifest/launcher.',
                            'Public candidate selection does not certify ownership or publication rights.'])
    out.mkdir()
    for name, content in sorted(payload.items()):
        destination = out / safe_relative(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(content)
    with (out / MANIFEST).open('xb') as stream:
        stream.write(encode(manifest))
    verify(out)
    return manifest


def verify(root):
    root = Path(root).absolute()
    no_links(root, root)
    manifest_path = root / MANIFEST
    no_links(manifest_path, root)
    manifest = strict_json(manifest_path.read_text(encoding='utf8'))
    fields = {'format', 'files', 'dependencies', 'runtime_abi', 'dependency_mode', 'source_selection',
              'outcome_version', 'evidence_policy', 'accuracy_validated', 'fresh_openkylin_accepted',
              'publication_authorized', 'limits'}
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest.get('format') != FORMAT or not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ValueError('invalid portable manifest')
    if manifest['outcome_version'] != 'v2' or manifest['evidence_policy'] != 'scoped-v1':
        raise ValueError('invalid candidate outcome or evidence policy')
    if manifest['dependency_mode'] != 'copied-runtime-files-not-pip-installation':
        raise ValueError('invalid candidate dependency mode')
    actual = set()
    for source in root.rglob('*'):
        no_links(source, root)
        if source.is_file():
            actual.add(source.relative_to(root).as_posix())
    if actual != set(manifest['files']) | {MANIFEST}:
        raise ValueError('missing or unmanifested bundle file')
    for name, expected in manifest['files'].items():
        safe_relative(name)
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected) or sha((root / name).read_bytes()) != expected:
            raise ValueError('bundle hash mismatch')
    if any(manifest.get(field) is not False for field in ('accuracy_validated', 'fresh_openkylin_accepted', 'publication_authorized')):
        raise ValueError('portable candidate cannot claim real acceptance')
    return manifest
