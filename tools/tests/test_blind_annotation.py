#!/usr/bin/env python3
"""Offline safety checks: independent projection, fail-closed evidence, package binding."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import blind_annotation as B


class BlindPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'private-agent-remember'
        (self.run / 'raw/task').mkdir(parents=True)
        self.sample = {'id': 'secret-sample', 'dimension': 'memory_recall',
                       'title': 'SECRET_TITLE', 'anchors': [{'desc': 'SECRET_RULE'}],
                       'expect': {'verdict': 'SECRET_VERDICT', 'reference_answer': 'SECRET_REFERENCE',
                                  'notes': 'SECRET_NOTES'},
                       'script': [{'session': 's1', 'phase': 'probe', 'time': '2026-01-01',
                                   'note': 'SECRET_SCRIPT_NOTE', 'turns': [{'text': 'Remember blue'}]}],
                       'fixture': [{'path': 'input.txt', 'content': 'blue'}]}
        self.paper = self.root / 'paper.jsonl'
        self.rows = []
        self.add('memory_read', 'env', 'memory', {'phase': 'before', 'entries_count': 0,
                 'coverage': {'files_scanned': 1, 'entries_scanned': 0, 'bytes_scanned': 0}}, '')
        self.add('message', 'user', 'dialogue', {'text': 'Remember blue', 'model': 'SECRET_MODEL'})
        self.add('message', 'agent', 'dialogue', {'text': 'x' * 2000, 'model': 'SECRET_MODEL'})
        self.add('file_write', 'agent', 'file', {'path': 'out.txt', 'op': 'create',
                 'before_content': None, 'after_content': 'blue', 'truncated': False,
                 'full_bytes': 4, 'stored_bytes': 4,
                 'content_sha256': hashlib.sha256(b'blue').hexdigest()})
        self.add('memory_read', 'env', 'memory', {'phase': 'after', 'entries_count': 1,
                 'coverage': {'files_scanned': 1, 'entries_scanned': 1, 'bytes_scanned': 4}},
                 json.dumps({'key': 'color', 'value': 'blue', 'source': 'SECRET_SOURCE'}))
        self.add('message', 'env', 'system', {'action': 'workspace_listing'},
                 json.dumps({'path': 'out.txt'}) + '\n' + json.dumps({'path': 'input.txt'}))
        self.manifest = {'run_id': 'SECRET_RUN', 'agents': [{'name': 'secret-agent', 'agent_kind': 'mock'}],
                         'tasks': [{'task_id': 'secret-agent-task', 'status': 'ok'}]}
        self.selection = self.root / 'selection.json'
        self.selection.write_text(json.dumps({'rows': [{'run_dir': str(self.run),
                                  'sample_id': self.sample['id'], 'task_id': 'secret-agent-task'}]}), encoding='utf8')
        self.sync()

    def add(self, kind, actor, source, payload, raw='{}'):
        i = len(self.rows)
        path = f'raw/task/{i}.jsonl'
        (self.run / path).write_text(raw, encoding='utf8')
        self.rows.append({'id': f'secret-{i}', 'seq': i, 'task_id': 'secret-agent-task',
                          'type': kind, 'actor': actor, 'source': source,
                          'session_id': 's1' if source == 'dialogue' else None,
                          'turn': 1 if source == 'dialogue' else None,
                          'payload': payload, 'raw_ref': {'path': path, 'line': 1}})

    def sync(self):
        self.paper.write_text(json.dumps(self.sample) + '\n', encoding='utf8')
        (self.run / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf8')
        (self.run / 'evidence.jsonl').write_text('\n'.join(json.dumps(r) for r in self.rows), encoding='utf8')
        # Poison file must never be read by independent selection or projection.
        (self.run / 'verdicts.jsonl').write_text('INVALID SECRET_VERDICT', encoding='utf8')

    def build(self, suffix=''):
        self.sync()
        return B.make_package(self.selection, self.paper, self.root / ('public' + suffix),
                              self.root / ('private' + suffix))

    def rejected(self):
        with self.assertRaises(B.BlindError):
            self.build()
        self.assertFalse((self.root / 'public').exists())
        self.assertFalse((self.root / 'private').exists())

    def test_whitelist_no_rule_identity_or_answers_no_truncation(self):
        self.build()
        text = (self.root / 'public/worklist.json').read_text(encoding='utf8')
        for secret in ('SECRET_', 'secret-agent', 'secret-sample', 'secret-0', 'soft_anchors_status', 'anchors_desc'):
            self.assertNotIn(secret, text)
        pack = json.loads(text)
        self.assertEqual(pack['purpose'], 'independent-human-draft')
        self.assertEqual(pack['release_status'], 'review_required')
        self.assertIn('x' * 2000, text)
        self.assertEqual({p.name for p in (self.root / 'public').iterdir()}, {'worklist.json'})
        private = json.loads((self.root / 'private/keymap.json').read_text(encoding='utf8'))
        self.assertEqual(private['worklist_sha256'], B.sha_file(self.root / 'public/worklist.json'))
        self.assertIn('secret-agent-task', json.dumps(private))

    def test_repack_has_disjoint_package_and_row_ids(self):
        self.build()
        self.build('2')
        a, b = [json.loads((self.root / f'public{s}/worklist.json').read_text(encoding='utf8')) for s in ('', '2')]
        self.assertNotEqual(a['package_id'], b['package_id'])
        self.assertNotEqual(a['rows'][0]['row_key'], b['rows'][0]['row_key'])

    def test_no_anchor_or_judge_execution(self):
        with patch('scoring.anchors.evaluate', side_effect=AssertionError('no anchor evaluation')), \
             patch('scoring.judge_client.build_visible_pack', side_effect=AssertionError('no judge view')):
            self.build()

    def test_missing_raw_rejected(self):
        (self.run / self.rows[0]['raw_ref']['path']).unlink()
        self.rejected()

    def test_raw_path_escape_rejected(self):
        self.rows[0]['raw_ref']['path'] = '../selection.json'
        self.rejected()

    def test_truncated_file_rejected(self):
        self.rows[3]['payload']['truncated'] = True
        self.rejected()

    def test_file_content_hash_mismatch_rejected(self):
        self.rows[3]['payload']['after_content'] = 'wrong'
        self.rejected()

    def test_missing_reply_rejected(self):
        self.rows.pop(2)
        self.rejected()

    def test_missing_snapshot_rejected(self):
        self.rows.pop(4)
        self.rejected()

    def test_unverified_real_probe_rejected(self):
        self.manifest['agents'][0]['agent_kind'] = 'real'
        self.rows[4]['payload']['entries_count'] = 0
        self.rows[4]['payload']['coverage']['entries_scanned'] = 0
        self.rejected()

    def test_failed_snapshot_rejected(self):
        self.rows[4]['payload']['error'] = 'probe failed'
        self.rejected()

    def test_snapshot_count_mismatch_rejected(self):
        self.rows[4]['payload']['entries_count'] = 3
        self.rejected()

    def test_missing_listing_rejected(self):
        self.rows.pop()
        self.rejected()

    def test_unknown_file_without_content_rejected(self):
        (self.run / self.rows[-1]['raw_ref']['path']).write_text('{"path":"unknown.txt"}', encoding='utf8')
        self.rejected()

    def test_non_ok_task_rejected(self):
        self.manifest['tasks'][0]['status'] = 'env_error'
        self.rejected()

    def test_duplicate_selection_rejected(self):
        s = json.loads(self.selection.read_text(encoding='utf8'))
        s['rows'] *= 2
        self.selection.write_text(json.dumps(s), encoding='utf8')
        self.rejected()

    def test_nested_private_dir_rejected(self):
        with self.assertRaises(B.BlindError):
            B.make_package(self.selection, self.paper, self.root / 'public', self.root / 'public/private')
        self.assertFalse((self.root / 'public').exists())

    def test_existing_output_not_overwritten(self):
        self.build()
        original = (self.root / 'public/worklist.json').read_bytes()
        with self.assertRaises(B.BlindError):
            self.build()
        self.assertEqual(original, (self.root / 'public/worklist.json').read_bytes())

    def label_file(self, label='remembered'):
        work = self.root / 'public/worklist.json'
        pack = json.loads(work.read_text(encoding='utf8'))
        row = {'package_id': pack['package_id'], 'worklist_sha256': B.sha_file(work),
               'row_key': pack['rows'][0]['row_key'], 'label': label, 'note': 'evidence E2'}
        path = self.root / 'labels.jsonl'
        path.write_text(json.dumps(row), encoding='utf8')
        return work, path, row

    def test_valid_labels_and_unassessable(self):
        self.build()
        for value in ('remembered', 'unassessable'):
            work, labels, _ = self.label_file(value)
            self.assertEqual(B.validate_labels(work, labels), [])

    def test_old_labels_cannot_join(self):
        self.build()
        work, labels, row = self.label_file()
        row.pop('package_id')
        labels.write_text(json.dumps(row), encoding='utf8')
        self.assertTrue(B.validate_labels(work, labels))

    def test_tampered_worklist_binding_rejected(self):
        self.build()
        work, labels, _ = self.label_file()
        work.write_text(work.read_text(encoding='utf8') + '\n', encoding='utf8')
        self.assertTrue(B.validate_labels(work, labels))

    def test_duplicate_missing_illegal_labels_rejected(self):
        self.build()
        work, labels, row = self.label_file('BAD')
        labels.write_text(json.dumps(row) + '\n' + json.dumps(row), encoding='utf8')
        self.assertTrue(B.validate_labels(work, labels))
        labels.write_text('', encoding='utf8')
        self.assertTrue(B.validate_labels(work, labels))

    def test_duplicate_evidence_id_rejected(self):
        self.rows[1]['id'] = self.rows[0]['id']
        self.rejected()

    def test_failed_health_event_rejected(self):
        self.add('message', 'env', 'system', {'action': 'health_after', 'ok': False})
        self.rejected()

    def test_listing_hash_mismatch_rejected(self):
        (self.run / self.rows[-1]['raw_ref']['path']).write_text(
            '{"path":"out.txt","sha256":"wrong"}\n{"path":"input.txt"}', encoding='utf8')
        self.rejected()

    def test_legacy_validator_cannot_bypass_binding(self):
        import annotate
        self.build()
        work, labels, _ = self.label_file()
        self.assertNotEqual(annotate.validate(work, labels), 0)

    def test_cli_handles_missing_input_without_traceback(self):
        import annotate
        self.assertEqual(annotate.main(['make-blind-worklist', '--selection', str(self.root / 'absent'),
                         '--paper', str(self.paper), '--public-dir', str(self.root / 'public'),
                         '--private-dir', str(self.root / 'private')]), 2)

    def test_selection_sample_mismatch_rejected(self):
        self.sample['script'][0]['turns'][0]['text'] = 'Another task'
        self.rejected()

    def test_system_identity_metadata_not_exposed(self):
        self.add('message', 'env', 'system', {'action': 'reset', 'ok': True,
                                            'policy': 'SECRET_POLICY'})
        self.build()
        self.assertNotIn('SECRET_POLICY', (self.root / 'public/worklist.json').read_text(encoding='utf8'))

    def test_time_normalization_facts_preserved(self):
        self.add('message', 'env', 'system', {'action': 'time_normalization', 'session': 's1',
                 'anchor': '2026-05-20T10:00:00+08:00', 'workspace_files': 1,
                 'memory': {'supported': False}, 'debug': 'SECRET_DEBUG'})
        self.build()
        text = (self.root / 'public/worklist.json').read_text(encoding='utf8')
        self.assertIn('time_normalization', text)
        self.assertIn('2026-05-20T10:00:00+08:00', text)
        self.assertNotIn('SECRET_DEBUG', text)

    def test_reversed_snapshots_rejected(self):
        self.rows[0]['payload']['phase'] = 'after'
        self.rows[4]['payload']['phase'] = 'before'
        self.rejected()

    def test_malformed_snapshot_rejected(self):
        (self.run / self.rows[4]['raw_ref']['path']).write_text('[]', encoding='utf8')
        self.rejected()


if __name__ == '__main__':
    unittest.main()
