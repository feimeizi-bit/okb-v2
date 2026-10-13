#!/usr/bin/env python3
"""Regression of the selfcheck CLI gates with synthetic subprocess outputs.

No scoring algorithms are tested here; the real batchA command covers the full
pipeline separately. These tests inject corruption at the verdict/expect boundary.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import selfcheck_locked as sc


class SelfcheckGates(unittest.TestCase):
    def invoke(self, change_expect=None, change_rows=None, write=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'suite.yaml').write_text('samples:\n- id: one\n', encoding='utf-8')
            cells = {t: {'pass': t == 'remember',
                         'verdict': 'remembered' if t == 'remember' else 'omitted',
                         'violated_hard_first': 'diagnostic'} for t in sc.TIERS}
            rows = {t: [{'sample_id': 'one', 'task_id': 'mock-one', 'status': 'ok',
                         'verdict': c['verdict'], 'judge_error': None, 'reason': 'diagnostic'}]
                    for t, c in cells.items()}
            paper = b'fake paper for gate tests\n'
            import hashlib
            expect = {'paper_sha256': hashlib.sha256(paper).hexdigest(),
                      'tiers': list(sc.TIERS), 'cells': {'one': copy.deepcopy(cells)}}
            if change_expect:
                change_expect(expect)
            if change_rows:
                change_rows(rows)
            target = root / 'expect.json'
            target.write_text(json.dumps(expect), encoding='utf-8')
            before = target.read_bytes()

            def fake_sh(args, tag):
                if tag == 'build':
                    Path(args[-1]).write_bytes(paper)
                elif tag == 'lint':
                    return '0 error'
                elif tag.startswith('simulate:'):
                    dest = Path(args[args.index('--runs-dir') + 1]) / args[args.index('--run-id') + 1]
                    dest.mkdir(parents=True)
                elif tag.startswith('score:'):
                    dest = Path(args[args.index('--run-dir') + 1]) / 'verdicts.jsonl'
                    dest.write_text('\n'.join(json.dumps(x) for x in rows[tag.split(':')[1]]), encoding='utf-8')
                return ''

            argv = ['--suite', 'suite.yaml', '--policy-prefix', 'policy-',
                    '--policy-suffix', 'v0.3', '--expect', 'expect.json']
            if write:
                argv.append('--write')
            with patch.object(sc, 'ROOT', root), patch.object(sc, 'sh', fake_sh), contextlib.redirect_stdout(io.StringIO()):
                rc = sc.main(argv)
            return rc, before == target.read_bytes()

    def test_clean_passes(self):
        self.assertEqual(self.invoke()[0], 0)

    def test_same_pass_different_verdict_fails(self):
        rc, _ = self.invoke(change_expect=lambda e: e['cells']['one']['coexist'].update(verdict='confused'))
        self.assertNotEqual(rc, 0)

    def test_missing_sample_fails(self):
        self.assertNotEqual(self.invoke(change_expect=lambda e: e['cells'].clear())[0], 0)

    def test_extra_sample_fails(self):
        self.assertNotEqual(self.invoke(change_expect=lambda e: e['cells'].update(ghost=copy.deepcopy(e['cells']['one'])))[0], 0)

    def test_extra_tier_fails(self):
        self.assertNotEqual(self.invoke(change_expect=lambda e: e['cells']['one'].update(ghost=e['cells']['one']['remember']))[0], 0)

    def test_tier_header_fails(self):
        self.assertNotEqual(self.invoke(change_expect=lambda e: e.update(tiers=['remember']))[0], 0)

    def test_boolean_not_integer(self):
        self.assertNotEqual(self.invoke(change_expect=lambda e: e['cells']['one']['remember'].update({'pass': 1}))[0], 0)

    def test_sha_drift(self):
        self.assertNotEqual(self.invoke(change_expect=lambda e: e.update(paper_sha256='wrong'))[0], 0)

    def test_duplicate_rows_fail(self):
        self.assertNotEqual(self.invoke(change_rows=lambda r: r['coexist'].append(dict(r['coexist'][0])))[0], 0)

    def test_unexpected_rows_fail(self):
        self.assertNotEqual(self.invoke(change_rows=lambda r: r['coexist'].append(dict(r['coexist'][0], sample_id='ghost')))[0], 0)

    def test_missing_rows_fail(self):
        self.assertNotEqual(self.invoke(change_rows=lambda r: r['coexist'].clear())[0], 0)

    def test_env_error_is_not_a_valid_weak_failure(self):
        def rows(r):
            r['coexist'][0].update(status='env_error', verdict=None)
        def exp(e):
            e['cells']['one']['coexist']['verdict'] = None
        self.assertNotEqual(self.invoke(change_rows=rows, change_expect=exp)[0], 0)

    def test_gamed_pass_fails_even_when_expected(self):
        def rows(r):
            r['gamed'][0]['verdict'] = 'remembered'
        def exp(e):
            e['cells']['one']['gamed'].update({'pass': True, 'verdict': 'remembered'})
        self.assertNotEqual(self.invoke(change_rows=rows, change_expect=exp)[0], 0)

    def test_gamed_skip_is_not_a_detected_failure(self):
        def rows(r):
            r['gamed'][0].update(status='skip', verdict=None)
        def exp(e):
            e['cells']['one']['gamed']['verdict'] = None
        self.assertNotEqual(self.invoke(change_rows=rows, change_expect=exp)[0], 0)

    def test_non_gamed_skip_allowed_when_expected(self):
        def rows(r):
            r['forget'][0].update(status='skip', verdict=None)
        def exp(e):
            e['cells']['one']['forget']['verdict'] = None
        self.assertEqual(self.invoke(change_rows=rows, change_expect=exp)[0], 0)

    def test_write_refuses_incomplete_rows_without_touching_expect(self):
        rc, untouched = self.invoke(change_rows=lambda r: r['forget'].clear(), write=True)
        self.assertNotEqual(rc, 0)
        self.assertTrue(untouched)


if __name__ == '__main__':
    unittest.main()
