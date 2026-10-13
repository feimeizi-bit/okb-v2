#!/usr/bin/env python3
"""Offline checks for verdict-stratified sampling, budget and mapping hygiene."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import annotate


class WorklistSampling(unittest.TestCase):
    def invoke(self, labels, cap=0):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root / 'run'
            run.mkdir()
            verdicts = [{'sample_id': f's{i:02}', 'task_id': f'mock-s{i:02}',
                         'status': 'ok', 'verdict': label} for i, label in enumerate(labels)]
            (run / 'verdicts.jsonl').write_text('\n'.join(json.dumps(r) for r in verdicts), encoding='utf-8')
            out = root / 'worklist.json'
            paper = [{'id': r['sample_id']} for r in verdicts]
            with patch.object(annotate, 'load_jsonl', return_value=paper), \
                 patch.object(annotate.EvidenceIndex, 'load', return_value=object()), \
                 patch.object(annotate, '_soft_outcomes', return_value=[object()]), \
                 patch.object(annotate, 'build_visible_pack', return_value=('', {'evidence': 'opaque'})), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = annotate.make_worklist([str(run)], 'unused', str(out), cap)
            work = json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
            mapping = json.loads((root / 'keymap.json').read_text(encoding='utf-8')) if out.exists() else None
            lookup = {r['task_id']: r['verdict'] for r in verdicts}
            kept = [lookup[x[2]] for x in mapping.values()] if mapping else []
            return rc, work, mapping, kept

    def test_minority_classes_represented(self):
        rc, _, _, kept = self.invoke(['remembered'] * 8 + ['omitted'] * 2 + ['wrongly_reused'], 6)
        self.assertEqual(rc, 0)
        self.assertEqual(set(kept), {'remembered', 'omitted', 'wrongly_reused'})
        self.assertEqual(len(kept), 6)
        self.assertEqual(kept.count('remembered'), 3)

    def test_remainder_filled(self):
        _, _, _, kept = self.invoke(['remembered'] * 8 + ['omitted'] * 8, 5)
        self.assertEqual(len(kept), 5)

    def test_cap_below_class_count_never_overflows(self):
        _, _, _, kept = self.invoke(sorted(annotate.TIERS5), 2)
        self.assertEqual(len(kept), 2)

    def test_empty_pool_fails_without_division_or_files(self):
        rc, work, _, _ = self.invoke([], 5)
        self.assertNotEqual(rc, 0)
        self.assertIsNone(work)

    def test_negative_cap_rejected_without_files(self):
        rc, work, _, _ = self.invoke(['remembered'], -1)
        self.assertNotEqual(rc, 0)
        self.assertIsNone(work)

    def test_metadata_not_in_annotator_worklist(self):
        _, work, mapping, kept = self.invoke(['remembered', 'omitted', 'confused'], 0)
        self.assertEqual(len(kept), 3)
        self.assertEqual(work['n_rows'], 3)
        for row in work['rows']:
            self.assertEqual(set(row), {'row_key', 'digest'})
            self.assertEqual(len(mapping[row['row_key']]), 3)
        self.assertEqual(set(mapping), {'R01', 'R02', 'R03'})

    def test_repeatable(self):
        a = self.invoke(['remembered'] * 4 + ['omitted'] * 2, 4)
        b = self.invoke(['remembered'] * 4 + ['omitted'] * 2, 4)
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
