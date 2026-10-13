#!/usr/bin/env python3
"""No network. Exact bytes, PII domains and raw/payload ordering checks."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
import audit_file_evidence as A
from scoring.index import EvidenceIndex
from engine.collectors import EvidenceWriter, sha256_text
from engine.workspace import Workspace


class FileAuditTests(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(A.explain('abc', A.fingerprint('abc'), [])['classification'], 'exact')

    def test_crlf_explanation(self):
        self.assertEqual(A.explain('甲\n乙\n', A.fingerprint('甲\r\n乙\r\n'), [])['classification'], 'newline_representation')

    def test_lf_explanation(self):
        self.assertEqual(A.explain('甲\r\n', A.fingerprint('甲\n'), [])['classification'], 'newline_representation')

    def test_materialized_pii_not_emitted(self):
        secret = 'SYNTHETIC-PRIVATE-VALUE'
        result = A.explain('<PII_TEST>', A.fingerprint(secret), [{'id': 'PII_TEST', 'value': secret}])
        self.assertEqual(result['classification'], 'pii_domain')
        self.assertNotIn(secret, json.dumps(result))

    def test_pii_plus_newline(self):
        result = A.explain('<PII_TEST>\n', A.fingerprint('SYNTHETIC\r\n'), [{'id': 'PII_TEST', 'value': 'SYNTHETIC'}])
        self.assertEqual(result['classification'], 'pii_and_newline')

    def test_same_size_not_enough(self):
        self.assertEqual(A.explain('abc', A.fingerprint('xyz'), [])['classification'], 'unresolved')

    def test_wrong_size_not_match(self):
        x = A.fingerprint('abc'); x['size'] = 4
        self.assertEqual(A.explain('abc', x, [])['classification'], 'unresolved')

    def test_unknown_placeholder_no_guess(self):
        self.assertEqual(A.explain('<PII_UNKNOWN>', A.fingerprint('secret'), [])['classification'], 'unresolved')

    def test_missing_fingerprint_fails(self):
        with self.assertRaises(A.AuditError):
            A.explain('abc', {'size': 3}, [])

    def test_existing_report_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'report.json'; p.write_text('keep', encoding='utf8')
            self.assertEqual(A.main(['--selection', 'missing', '--paper', 'missing', '--out', str(p)]), 2)
            self.assertEqual(p.read_text(encoding='utf8'), 'keep')

    def case(self, td):
        root = Path(td)
        writer = EvidenceWriter('test', root)
        payload = {'path': 'f.txt', 'op': 'create', 'after_content': 'abc',
                   'content_sha256': sha256_text(''), 'full_bytes': 0, 'stored_bytes': 0, 'truncated': False}
        def finish(line):
            line['payload'].update(content_sha256=sha256_text('abc'), full_bytes=3, stored_bytes=3)
        event = writer.emit(task_id='agent-s', source='file', type='file_write', actor='agent', payload=payload, postprocess=finish)
        raw = writer.raw.write_jsonl('agent-s', 'listing.jsonl', [dict(A.fingerprint('abc'), path='f.txt')])
        listing = writer.emit(task_id='agent-s', source='system', type='message', actor='env', payload={'action': 'workspace_listing'}, raw_ref=raw)
        manifest = {'run_id': 'test', 'agents': [{'name': 'agent'}]}
        return {'id': 's'}, EvidenceIndex(root, manifest, [event, listing])

    def test_real_writer_fallback_matches_final_payload(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            r = A.audit_task(s, idx, 'agent-s')
            self.assertEqual(r['files'][0]['classification'], 'exact')
            w = r['writes'][0]
            self.assertTrue(w['raw_content_equal'])
            self.assertTrue(w['payload_self_consistent'])
            self.assertEqual(w['raw_differing_fields'], [])
            self.assertFalse(r['release_authorized'])

    def test_historical_pre_postprocess_payload_still_reported(self):
        # Reproduce historical shape only in a temporary fixture; never repair it.
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            path = Path(td) / idx.rows[0]['raw_ref']['path']
            raw = json.loads(path.read_text(encoding='utf8'))
            raw.update(content_sha256=sha256_text(''), full_bytes=0, stored_bytes=0)
            path.write_text(json.dumps(raw) + '\n', encoding='utf8')
            before = path.read_bytes()
            r = A.audit_task(s, idx, 'agent-s')
            self.assertEqual(r['writes'][0]['raw_differing_fields'],
                             ['content_sha256', 'full_bytes', 'stored_bytes'])
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(r['release_authorized'])

    def test_payload_corruption_reported(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            idx.rows[0]['payload']['content_sha256'] = '0' * 64
            self.assertFalse(A.audit_task(s, idx, 'agent-s')['writes'][0]['payload_self_consistent'])

    def test_missing_raw_fails(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            (Path(td) / idx.rows[0]['raw_ref']['path']).unlink()
            with self.assertRaises(A.AuditError): A.audit_task(s, idx, 'agent-s')

    def test_escaping_raw_fails(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            idx.rows[0]['raw_ref']['path'] = 'raw/../../outside'
            with self.assertRaises(A.AuditError): A.audit_task(s, idx, 'agent-s')

    def test_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            idx.rows[0]['payload']['truncated'] = True
            with self.assertRaises(A.AuditError): A.audit_task(s, idx, 'agent-s')

    def test_duplicate_listing_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            r = dict(idx.rows[-1], id='unique', seq=2); idx.rows.append(r)
            with self.assertRaises(A.AuditError): A.audit_task(s, idx, 'agent-s')

    def test_report_cannot_write_inside_audited_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, idx = self.case(root / 'run')
            (idx.run_dir / 'manifest.json').write_text(json.dumps(idx.manifest), encoding='utf8')
            paper = root / 'paper.jsonl'; paper.write_text(json.dumps(s), encoding='utf8')
            selection = root / 'selection.json'
            selection.write_text(json.dumps({'rows': [{'run_dir': str(idx.run_dir),
                'sample_id': 's', 'task_id': 'agent-s'}]}), encoding='utf8')
            target = idx.run_dir / 'audit.json'
            self.assertEqual(A.main(['--selection', str(selection), '--paper', str(paper), '--out', str(target)]), 2)
            self.assertFalse(target.exists())

    def test_unresolved_report_has_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, idx = self.case(root / 'run')
            (idx.run_dir / 'manifest.json').write_text(json.dumps(idx.manifest), encoding='utf8')
            p = idx.run_dir / idx.rows[-1]['raw_ref']['path']
            p.write_text(json.dumps(dict(A.fingerprint('xyz'), path='f.txt')), encoding='utf8')
            paper = root / 'paper.jsonl'; paper.write_text(json.dumps(s), encoding='utf8')
            selection = root / 'selection.json'
            selection.write_text(json.dumps({'rows': [{'run_dir': str(idx.run_dir),
                'sample_id': 's', 'task_id': 'agent-s'}]}), encoding='utf8')
            self.assertEqual(A.main(['--selection', str(selection), '--paper', str(paper), '--out', str(root / 'audit.json')]), 1)

    def test_invalid_raw_line_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            s, idx = self.case(td)
            idx.rows[0]['raw_ref']['line'] = 2
            with self.assertRaises(A.AuditError): A.audit_task(s, idx, 'agent-s')

    def test_fixture_native_bytes_observed_without_assuming_platform(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(Path(td)); ws.materialize([{'path': 'f.txt', 'content': 'one\ntwo\n'}])
            r = A.explain('one\ntwo\n', ws.listing()[0], [])
            self.assertIn(r['classification'], ('exact', 'newline_representation'))
            self.assertEqual(r['listing']['sha256'], hashlib.sha256((Path(td) / 'f.txt').read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
