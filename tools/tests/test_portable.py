"""Portable packaging/routing controls, not real-Agent or semantic accuracy tests."""
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
from portable.bundle import DEPENDENCIES, MANIFEST, build, encode, no_links, safe_relative, sha, strict_json, verify


def fake_dependencies(root):
    root.mkdir()
    for name, module in DEPENDENCIES.items():
        relative = Path(module + '.py')
        (root / relative).write_text('fixture = True\n', encoding='utf8')
        info = root / (name.replace('-', '_') + '-1.0.0.dist-info')
        info.mkdir()
        (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + name + '\nVersion: 1.0.0\n', encoding='utf8')
        records = [relative.as_posix(), (info / 'METADATA').relative_to(root).as_posix(), (info / 'RECORD').relative_to(root).as_posix()]
        (info / 'RECORD').write_text(''.join(name + ',,\n' for name in records), encoding='utf8')


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.dependencies = self.directory / 'dependencies'
        fake_dependencies(self.dependencies)
        self.source = self.directory / 'source'
        for folder in ('okb', 'tools/portable', 'engine', 'schemas', 'samples', 'docs', 'runs', 'notes', 'tools/tests'):
            (self.source / folder).mkdir(parents=True, exist_ok=True)
        for folder in ('okb', 'tools/portable'):
            for source in (ROOT / folder).glob('*.py'):
                shutil.copyfile(source, self.source / folder / source.name)
        for name in ('okb_portable.py', 'pyproject.toml', 'docs/portable-offline-v1.md', 'samples/semantic-robustness.v1.json'):
            shutil.copyfile(ROOT / name, self.source / name)
        (self.source / 'engine/__init__.py').write_text('', encoding='utf8')
        (self.source / 'schemas/test.json').write_text('{}', encoding='utf8')
        for name in ('.env', 'runs/private.json', 'notes/hidden-labels.json', 'tools/tests/private.py', 'tools/_temporary.py'):
            (self.source / name).write_text('excluded-private-marker', encoding='utf8')
        self.out = self.directory / 'bundle space'

    def make(self):
        return build(self.source, self.dependencies, self.out)

    def launch(self, *arguments):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        environment.pop('PYTHONPATH', None)
        return subprocess.run([sys.executable, '-I', str(self.out / 'okb_portable.py'), *arguments],
                              cwd=self.directory, env=environment, capture_output=True, text=True, timeout=40)

    def test_manifest_and_dependency_lock(self):
        manifest = self.make()
        self.assertEqual(verify(self.out), manifest)
        self.assertEqual(len(manifest['dependencies']), 7)
        self.assertTrue(all(item['version'] == '1.0.0' for item in manifest['dependencies']))
        self.assertIn('okb/__main__.py', manifest['files'])
        self.assertEqual(len((self.out / 'requirements.portable.lock.txt').read_text().splitlines()), 7)

    def test_private_and_test_material_excluded(self):
        manifest = self.make()
        self.assertFalse(any('private' in name or name.startswith(('runs/', 'notes/')) or name == '.env' or '/tests/' in name or name.endswith('_temporary.py') for name in manifest['files']))

    def test_existing_output_preserved(self):
        self.out.mkdir()
        sentinel = self.out / 'sentinel'
        sentinel.write_text('preserve')
        with self.assertRaises(ValueError):
            self.make()
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_output_cannot_be_in_source(self):
        with self.assertRaises(ValueError):
            build(self.source, self.dependencies, self.source / 'bundle')
        self.assertFalse((self.source / 'bundle').exists())

    def test_missing_dependency_rejected_before_output(self):
        shutil.rmtree(self.dependencies / 'attrs-1.0.0.dist-info')
        with self.assertRaises(ValueError):
            self.make()
        self.assertFalse(self.out.exists())

    def test_missing_dependency_record_rejected(self):
        (self.dependencies / 'attrs-1.0.0.dist-info/RECORD').unlink()
        with self.assertRaises(ValueError):
            self.make()

    def test_missing_record_member_rejected(self):
        (self.dependencies / 'attrs.py').unlink()
        with self.assertRaises(OSError):
            self.make()

    def test_dependency_record_cannot_escape(self):
        record = self.dependencies / 'attrs-1.0.0.dist-info/RECORD'
        record.write_text(record.read_text() + '../outside.py,,\n')
        with self.assertRaises(ValueError):
            self.make()

    def test_known_external_jsonschema_script_is_recorded_not_read_or_copied(self):
        record = self.dependencies / 'jsonschema-1.0.0.dist-info/RECORD'
        record.write_text(record.read_text() + '../../bin/jsonschema.exe,,\n')
        manifest = self.make()
        dependency = next(item for item in manifest['dependencies'] if item['name'] == 'jsonschema')
        self.assertEqual(dependency['excluded_external_scripts'], ['../../bin/jsonschema.exe'])
        self.assertFalse(any('jsonschema.exe' in name for name in manifest['files']))

    def test_duplicate_distribution_rejected(self):
        duplicate = self.dependencies / 'attrs-2.0.0.dist-info'
        shutil.copytree(self.dependencies / 'attrs-1.0.0.dist-info', duplicate)
        with self.assertRaises(ValueError):
            self.make()

    def test_direct_url_and_bytecode_not_copied(self):
        info = self.dependencies / 'attrs-1.0.0.dist-info'
        (info / 'direct_url.json').write_text('{"url":"private-location"}')
        cached = self.dependencies / '__pycache__'
        cached.mkdir()
        (cached / 'attrs.pyc').write_bytes(b'fixture')
        record = info / 'RECORD'
        record.write_text(record.read_text() + 'attrs-1.0.0.dist-info/direct_url.json,,\n__pycache__/attrs.pyc,,\n')
        manifest = self.make()
        self.assertFalse(any('direct_url' in name or '.pyc' in name for name in manifest['files']))

    def test_source_credential_pattern_rejected(self):
        (self.source / 'tools/unsafe.py').write_text("value = 'sk-" + 'x' * 32 + "'\n")
        with self.assertRaises(ValueError):
            self.make()
        self.assertFalse(self.out.exists())

    def test_symlink_source_rejected(self):
        link = self.source / 'schemas/link.json'
        try:
            link.symlink_to(self.source / 'schemas/test.json')
        except OSError:
            self.skipTest('OS does not permit an unprivileged test symlink')
        with self.assertRaises(ValueError):
            self.make()

    def test_tampered_member_rejected(self):
        self.make()
        (self.out / 'schemas/test.json').write_text('{"tampered":true}')
        with self.assertRaises(ValueError):
            verify(self.out)

    def test_missing_member_rejected(self):
        self.make()
        (self.out / 'schemas/test.json').unlink()
        with self.assertRaises(ValueError):
            verify(self.out)

    def test_added_member_rejected(self):
        self.make()
        (self.out / 'extra.py').write_text('pass')
        with self.assertRaises(ValueError):
            verify(self.out)

    def test_default_check_from_foreign_directory_with_spaces(self):
        self.make()
        result = self.launch('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['dependency_versions_verified'])
        self.assertFalse(list(self.out.rglob('__pycache__')))
        self.assertEqual(self.launch().returncode, 0)

    def test_changed_abi_rejected(self):
        manifest = self.make()
        manifest['runtime_abi']['cache_tag'] = 'different-python-abi'
        (self.out / MANIFEST).write_bytes(encode(manifest))
        self.assertEqual(self.launch('--check').returncode, 2)

    def test_declared_pin_and_metadata_mismatch_rejected(self):
        manifest = self.make()
        manifest['dependencies'][0]['version'] = '9.9.9'
        (self.out / MANIFEST).write_bytes(encode(manifest))
        self.assertEqual(self.launch('--check').returncode, 2)

    def test_paid_flags_and_real_execution_commands_rejected(self):
        self.make()
        for arguments in (['score-vnext', '--live'], ['score-vnext', '--live=true'], ['score-vnext', '--li'],
                          ['score-vnext', '--auth'], ['score-vnext', '--api-k=FIXTURE_KEY'],
                          ['score-vnext', '--authorize-paid-calls'], ['score-vnext', '--api-key-env', 'FIXTURE_KEY'],
                          ['run-agent'], ['build-portable']):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.launch(*arguments).returncode, 2)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(ValueError):
            strict_json('{"key":1,"key":2}')

    def test_nonstandard_json_constants_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json('{"key":' + value + '}')

    def test_candidate_cannot_claim_accuracy_fresh_acceptance_or_publication(self):
        manifest = self.make()
        for field in ('accuracy_validated', 'fresh_openkylin_accepted', 'publication_authorized'):
            with self.subTest(field=field):
                changed = dict(manifest, **{field: True})
                (self.out / MANIFEST).write_bytes(encode(changed))
                with self.assertRaises(ValueError):
                    verify(self.out)

    def test_manifest_policy_and_unknown_fields_rejected(self):
        manifest = self.make()
        for changed in (dict(manifest, evidence_policy='unverified'), dict(manifest, unknown=True)):
            (self.out / MANIFEST).write_bytes(encode(changed))
            with self.assertRaises(ValueError):
                verify(self.out)

    def test_windows_reparse_attribute_rejected_without_creating_real_link(self):
        from types import SimpleNamespace
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0, st_file_attributes=0x400)):
            with self.assertRaises(ValueError):
                no_links(self.source, self.source)

    def test_path_traversal_and_platform_paths_rejected(self):
        for name in ('../secret', '/secret', 'C:/secret', 'vendor\\secret', 'x//y', './x', 'x/../y'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_relative(name)

    def test_audit_hook_blocks_network_and_subprocess_before_dispatch(self):
        source = "import sys,socket,subprocess;sys.path.insert(0," + repr(str(ROOT / 'tools')) + ");from portable.runtime import offline;sys.addaudithook(offline);"
        for expression in ("socket.getaddrinfo('127.0.0.1',1)", "socket.socket().connect(('127.0.0.1',1))", "subprocess.run([sys.executable,'--version'])"):
            program = source + '\ntry:\n ' + expression + "\nexcept RuntimeError:\n print('blocked')\nelse:\n raise AssertionError('guard did not block')\n"
            result = subprocess.run([sys.executable, '-I', '-c', program], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'blocked')


class RealDependencyIntegrationTests(unittest.TestCase):
    def test_relocated_bundle_uses_only_its_vendor_and_preserves_memory_refusal(self):
        import yaml
        dependency_dir = Path(yaml.__file__).resolve().parents[1]
        names = {item.metadata['Name'].lower().replace('_', '-') for item in metadata.distributions(path=[str(dependency_dir)])}
        if not set(DEPENDENCIES) <= names:
            self.skipTest('dependencies must be colocated for portable integration')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source_bundle = directory / 'first bundle'
            build(ROOT, dependency_dir, source_bundle)
            bundle = directory / 'relocated bundle space'
            shutil.copytree(source_bundle, bundle)
            consumer = directory / 'foreign consumer directory'
            consumer.mkdir()
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
            environment.pop('PYTHONPATH', None)
            output = consumer / 'new selfcheck'
            command = [sys.executable, '-I', str(bundle / 'okb_portable.py'), '--selfcheck', '--out-dir', str(output)]
            result = subprocess.run(command, cwd=consumer, env=environment, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr + (output / 'pipeline.log').read_text() if (output / 'pipeline.log').exists() else result.stderr)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['task_outcomes']['portable_memory'], 'unassessable')
            self.assertTrue(summary['same_run_replay_byte_identical'])
            self.assertTrue(summary['report_trace_verified'])
            self.assertFalse(summary['semantic_reliability_validated'])
            complete = json.loads((output / 'complete.json').read_text())
            self.assertTrue(all(sha((output / name).read_bytes()) == expected for name, expected in complete.items()))
            verify(bundle)
            repeated = subprocess.run(command, cwd=consumer, env=environment, capture_output=True, text=True, timeout=120)
            self.assertEqual(repeated.returncode, 2)
            self.assertFalse((bundle / 'vendor/yaml/__pycache__').exists())


if __name__ == '__main__':
    unittest.main()
