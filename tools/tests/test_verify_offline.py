"""Maintainer verifier controls; authored mini-repository fixtures, not real acceptance."""
from dataclasses import replace
import contextlib
import io
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
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).parent)]
from test_portable import fake_dependencies
from verification.offline import Inputs, LEGACY_FILES, baseline, differences, discovery, environment, prepare_inputs, run_command, sha, verify
from verify_offline import main


def node_path():
    found = shutil.which('node')
    candidates = [Path(found)] if found else []
    candidates.append(Path(sys.executable).resolve().parents[1] / 'node/bin/node.exe')
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / 'authored source space'
        for folder in ('tools/tests', 'engine/tests', 'samples', 'schemas', 'runs/history', 'notes', 'okb'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        for name in ('tools/tests/test_unit.py', 'engine/tests/test_unit.py'):
            (self.root / name).write_text("print('authored unit control')\n", encoding='utf8')
        (self.root / 'tools/tests/test_unit.cjs').write_text("console.log('authored JS control');\n", encoding='utf8')
        for name in ('pyproject.toml', 'okb_portable.py', 'paper.jsonl', 'plan.json', 'samples/corpus.json', 'runs/history/raw.json', 'notes/p5-preregistration-v1.md'):
            (self.root / name).write_text('authored fixture\n', encoding='utf8')
        self.expected_bytes = {name: (json.dumps({'authored_fixture': name}) + '\n').encode('utf8') for name in LEGACY_FILES}
        for tool, folder in (('score_four_layer.py', 'd1-prepare'), ('semantic_robustness.py', 'robustness-prepare')):
            selected = {Path(name).name: value.decode('utf8') for name, value in self.expected_bytes.items() if name.startswith(folder + '/')}
            program = "from pathlib import Path\nimport sys,json,hashlib\nout=Path(sys.argv[sys.argv.index('--out-dir')+1]);out.mkdir()\n"
            program += 'members=' + repr(selected) + '\nfor name,content in members.items():\n (out/name).write_bytes(content.encode("utf8"))\n'
            program += 'marker={name+"_sha256":hashlib.sha256((out/(name+".json")).read_bytes()).hexdigest() for name in ("summary","results")}\n'
            program += '(out/"complete.json").write_text(json.dumps(marker))\nraise SystemExit(1)\n'
            (self.root / 'tools' / tool).write_text(program, encoding='utf8')
        self.baseline = self.root / 'notes/baseline.json'
        self.baseline.write_text(json.dumps({'legacy_compatibility': [dict(file=name, sha256=__import__('hashlib').sha256(content).hexdigest()) for name, content in self.expected_bytes.items()]}), encoding='utf8')
        self.deps = self.directory / 'authored dependencies'
        fake_dependencies(self.deps)
        self.node = node_path()
        self.inputs = Inputs(source_root=self.root.resolve(), dependency_dir=self.deps.resolve(),
            node_executable=self.node or Path(sys.executable).resolve(), legacy_paper=self.root / 'paper.jsonl',
            legacy_plan=self.root / 'plan.json', legacy_run=self.root / 'runs/history', legacy_baseline=self.baseline,
            semantic_corpus=self.root / 'samples/corpus.json', out=self.directory / 'new verification space', timeout=30)

    def execute(self, inputs=None):
        if not self.node:
            self.skipTest('actual Node unavailable for authored end-to-end verifier control')
        with contextlib.redirect_stdout(io.StringIO()):
            return verify(inputs or self.inputs)

    def test_discovers_both_python_trees_and_javascript(self):
        python, javascript = discovery(self.root)
        self.assertEqual(len(python), 2)
        self.assertEqual(len(javascript), 1)

    def test_missing_test_tree_cannot_claim_zero_test_success(self):
        (self.root / 'engine/tests/test_unit.py').unlink()
        with self.assertRaises(ValueError):
            discovery(self.root)

    def test_javascript_is_mandatory(self):
        (self.root / 'tools/tests/test_unit.cjs').unlink()
        with self.assertRaises(ValueError):
            discovery(self.root)

    def test_missing_historical_material_before_output(self):
        (self.root / 'paper.jsonl').unlink()
        with self.assertRaises(ValueError):
            prepare_inputs(self.inputs)
        self.assertFalse(self.inputs.out.exists())

    def test_missing_dependency_before_output(self):
        (self.deps / 'attrs-1.0.0.dist-info/RECORD').unlink()
        with self.assertRaises(ValueError):
            prepare_inputs(self.inputs)
        self.assertFalse(self.inputs.out.exists())

    def test_output_existing_preserved(self):
        self.inputs.out.mkdir()
        sentinel = self.inputs.out / 'sentinel'
        sentinel.write_text('preserve')
        with self.assertRaises(ValueError):
            prepare_inputs(self.inputs)
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_output_may_not_contain_source_or_be_inside_source(self):
        for out in (self.directory, self.root / 'new'):
            with self.subTest(out=out), self.assertRaises(ValueError):
                prepare_inputs(replace(self.inputs, out=out))

    def test_timeout_must_be_positive_integer(self):
        for timeout in (0, -1, True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                prepare_inputs(replace(self.inputs, timeout=timeout))

    def test_baseline_requires_six_unique_known_hashes(self):
        self.assertEqual(set(baseline(self.baseline)), set(LEGACY_FILES))
        document = json.loads(self.baseline.read_text())
        document['legacy_compatibility'][1] = document['legacy_compatibility'][0]
        self.baseline.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            baseline(self.baseline)

    def test_baseline_cannot_escape_output_or_use_bad_digest(self):
        document = json.loads(self.baseline.read_text())
        for field, value in (('file', '../secret'), ('sha256', 'invalid')):
            changed = json.loads(json.dumps(document))
            changed['legacy_compatibility'][0][field] = value
            self.baseline.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                baseline(self.baseline)

    def test_environment_removes_credentials_and_ambient_pythonpath(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'unused-authored-control', 'OTHER_TOKEN': 'unused-control', 'PYTHONPATH': 'ambient-fallback'}):
            env = environment(Path('guard'), Path('deps'), Path('source'))
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('OTHER_TOKEN', env)
        self.assertNotIn('ambient-fallback', env['PYTHONPATH'])
        self.assertTrue(env['PYTHONPATH'].startswith('guard' + os.pathsep))

    def test_drift_detects_added_removed_and_changed_paths(self):
        self.assertEqual(differences({'old': 'x', 'same': 'x', 'changed': 'x'}, {'new': 'x', 'same': 'x', 'changed': 'y'}), ['changed', 'new', 'old'])

    def test_timeout_recorded_without_success(self):
        self.inputs.out.mkdir()
        with patch('verification.offline.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 1)):
            result = run_command('timeout-control', ['unused'], out=self.inputs.out, root=self.root, env={}, timeout=1)
        self.assertEqual(result['exit'], 'timeout')
        self.assertFalse(result['passed'])

    def test_launch_failure_recorded(self):
        self.inputs.out.mkdir()
        with patch('verification.offline.subprocess.run', side_effect=OSError('fixture')):
            result = run_command('launch-control', ['unused'], out=self.inputs.out, root=self.root, env={}, timeout=1)
        self.assertEqual(result['exit'], 'launch-error')
        self.assertFalse(result['passed'])

    def test_guard_preflight_failure_stops_before_tests(self):
        def failed(label, command, **options):
            log = options['out'] / 'preflight.log'
            log.write_text('authored failed preflight')
            return dict(test=label, exit=1, expected_exit=0, passed=False, log=str(log), log_sha256=sha(log))
        with patch('verification.offline.run_command', side_effect=failed) as runner, self.assertRaises(ValueError):
            verify(self.inputs)
        self.assertEqual(runner.call_count, 1)
        self.assertFalse((self.inputs.out / 'complete.json').exists())

    def test_complete_authored_chain_and_frozen_hashes(self):
        summary = self.execute()
        self.assertTrue(summary['passed'])
        self.assertTrue(summary['guard_preflight_passed'])
        self.assertTrue(summary['legacy_byte_compatibility_verified'])
        self.assertTrue(summary['protected_unchanged'])
        self.assertEqual(summary['python_suites'], 2)
        self.assertEqual(summary['javascript_suites'], 1)
        complete = json.loads((self.inputs.out / 'complete.json').read_text())
        self.assertTrue(complete['passed'])
        self.assertTrue(all(sha(self.inputs.out / name) == digest for name, digest in complete['files'].items()))
        self.assertFalse(summary['semantic_reliability_validated'])
        self.assertIsNone(summary['real_judge_http_calls'])
        self.assertFalse(summary['judge_http_calls_measured'])
        self.assertFalse(summary['verifier_dispatches_judge_http'])

    def test_baseline_mismatch_keeps_failed_outputs(self):
        document = json.loads(self.baseline.read_text())
        document['legacy_compatibility'][0]['sha256'] = '0' * 64
        self.baseline.write_text(json.dumps(document))
        summary = self.execute()
        self.assertFalse(summary['passed'])
        self.assertFalse(summary['legacy_byte_compatibility_verified'])
        self.assertTrue((self.inputs.out / 'd1-prepare/results.json').is_file())
        self.assertFalse(json.loads((self.inputs.out / 'complete.json').read_text())['passed'])

    def test_script_failure_cannot_be_hidden(self):
        (self.root / 'tools/tests/test_unit.py').write_text('raise SystemExit(1)\n')
        summary = self.execute()
        self.assertFalse(summary['passed'])
        self.assertFalse(summary['all_expected_exits'])
        self.assertTrue(summary['legacy_byte_compatibility_verified'])

    def test_added_protected_input_is_detected(self):
        added = self.root / 'samples/added.json'
        (self.root / 'tools/tests/test_unit.py').write_text('from pathlib import Path\nPath(' + repr(str(added)) + ').write_text("authored mutation")\n')
        summary = self.execute()
        self.assertFalse(summary['passed'])
        self.assertIn(str(added), summary['changed_protected_files'])

    def test_deleted_explicit_input_is_recorded_not_silently_ignored(self):
        (self.root / 'tools/tests/test_unit.py').write_text('from pathlib import Path\nPath(' + repr(str(self.inputs.legacy_paper)) + ').unlink()\n')
        summary = self.execute()
        self.assertFalse(summary['passed'])
        self.assertIn(str(self.inputs.legacy_paper), summary['changed_protected_files'])

    def test_invalid_complete_marker_fails_even_when_frozen_bytes_match(self):
        script = self.root / 'tools/score_four_layer.py'
        script.write_text(script.read_text().replace('json.dumps(marker)', '"{}"'))
        summary = self.execute()
        self.assertFalse(summary['passed'])
        self.assertFalse(summary['prepare_outputs_valid'])
        self.assertTrue(summary['legacy_byte_compatibility_verified'])

    def test_dependency_drift_fails_independently_of_tests(self):
        target = self.deps / 'attrs.py'
        (self.root / 'tools/tests/test_unit.py').write_text('from pathlib import Path\nPath(' + repr(str(target)) + ').write_text("authored change")\n')
        summary = self.execute()
        self.assertFalse(summary['passed'])
        self.assertIn(str(target), summary['changed_dependency_files'])

    def test_private_bundle_without_test_trees_rejected(self):
        (self.root / 'tools/tests/test_unit.py').unlink()
        with self.assertRaises(ValueError):
            prepare_inputs(self.inputs)
        self.assertFalse(self.inputs.out.exists())

    def test_cli_requires_explicit_inputs_and_rejects_abbreviations(self):
        for arguments in ([], ['--dep', 'fixture']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(arguments)
            self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
