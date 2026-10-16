"""scoped-v2 tiered evidence gate: labelled acceptance, never blanket trust.

Covers the two semantics that replaced the blanket veto:
1. historical raw whose fingerprint fields were archived before the fingerprint pass
   (sha256 of empty bytes + 0 counts) is accepted with a representation label, while a
   raw whose *content* differs still rejects the task;
2. memory evidence is graded T0-T3 instead of vetoed, T0 stays unassessable, and a
   conclusion resting on T1 goes to the human review queue.
Not a model-accuracy test.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from engine.collectors import EvidenceWriter, now_iso
from engine.observation import file_scope, partial_receipt
from scoring.four_layer import score_task
from scoring.index import EvidenceIndex
from scoring.outcome_v2 import project_row
from scoring.scoped_evidence import EMPTY_SHA256, TIER_BOUND, TIER_DECLARED, project
from blind_annotation import BlindError

EMPTY_SHA = hashlib.sha256(b'').hexdigest()


class ScopedV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer = EvidenceWriter('run-v2', self.root)
        self.sample = dict(id='s', dimension='long_term_retention',
                           script=[dict(session='s1', turns=[{'text': 'Remember allergy and write output.'}])],
                           anchors=[{'id': 'A1'}, {'id': 'A2'}])
        self.rows = []
        self.memory = dict(id='memory', layer='semantic', scope='memory_after',
                           requirement='User has peanut allergy.', basis='s1.t1', on_failure='omitted')
        self.file = dict(id='file', layer='objective', op='file_exists', path='required.json',
                         basis='s1.t1', on_failure='confused')
        self.plan = dict(sample_id='s', checks=[self.memory, self.file], anchor_disposition={
            'A1': dict(role='semantic', checks=['memory'], reason='memory fact'),
            'A2': dict(role='objective', checks=['file'], reason='required output')})
        self.snap('before')
        self.emit('dialogue', 'message', 'user', {'text': self.sample['script'][0]['turns'][0]['text']},
                  session_id='s1', turn=1,
                  raw_ref=self.writer.raw.write_text('a-s', 'request.txt', self.sample['script'][0]['turns'][0]['text']))
        self.emit('dialogue', 'message', 'agent', {'text': 'Done'}, session_id='s1', turn=1,
                  raw_ref=self.writer.raw.write_text('a-s', 'reply.txt', 'Done'))
        self.snap('after')
        self.listing = {}

    def emit(self, source, kind, actor, payload, **kwargs):
        r = self.writer.emit(task_id='a-s', source=source, type=kind, actor=actor, payload=payload, **kwargs)
        self.rows.append(r)
        return r

    def snap(self, phase):
        return self.emit('memory', 'memory_read', 'env', dict(phase=phase, via='probe', entries_count=0,
                         coverage=dict(files_scanned=0, entries_scanned=0, bytes_scanned=0, sources=[])),
                         raw_ref=self.writer.raw.write_jsonl('a-s', phase + '.jsonl', []))

    def write(self, name, content):
        data = content.encode('utf8')
        return self.emit('file', 'file_write', 'agent', dict(path=name, op='create', after_content=content,
                         before_content=None, full_bytes=len(data), stored_bytes=len(data), truncated=False,
                         content_sha256=hashlib.sha256(data).hexdigest()))

    def finalize(self):
        """Final workspace listing over every artifact written so far."""
        files = [dict(path=n, size=v[0], sha256=v[1]) for n, v in self.listing.items()]
        self.emit('system', 'message', 'env', {'action': 'workspace_listing', 'files': len(files)},
                  raw_ref=self.writer.raw.write_jsonl('a-s', 'listing.jsonl', files))
        self.manifest = dict(run_id='run-v2', agents=[{'name': 'a', 'agent_kind': 'real'}],
                             tasks=[{'task_id': 'a-s', 'status': 'ok'}])

    def write_tracked(self, name, content):
        r = self.write(name, content)
        data = content.encode('utf8')
        self.listing[name] = (len(data), hashlib.sha256(data).hexdigest())
        return r

    def raw(self, r):
        return self.root / r['raw_ref']['path']

    def idx(self):
        return EvidenceIndex(self.root, self.manifest, self.rows)

    def score(self, policy='scoped-v2', judge=None):
        row = score_task(self.sample, self.idx(), 'a-s', self.plan, plan_sha256='test',
                         judge=judge, evidence_policy=policy)
        return project_row(row, self.plan['checks'])

    # ---- 1. legacy representation is labelled, not trusted blindly ----

    def test_current_raw_is_exact_and_unlabelled(self):
        self.write_tracked('required.json', '{"ok":true}')
        self.finalize()
        r = self.score()
        self.assertTrue(r['layer1']['valid'])
        self.assertEqual(r['layer1']['artifact_representations'], {})

    def test_pre_fingerprint_raw_accepted_with_label_in_v2(self):
        row = self.write_tracked('required.json', '{"ok":true}')
        stale = json.loads(self.raw(row).read_text('utf-8'))
        stale.update(content_sha256=EMPTY_SHA, full_bytes=0, stored_bytes=0)
        self.raw(row).write_text(json.dumps(stale, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
        self.finalize()
        with self.assertRaises(BlindError) as ctx:
            project(self.sample, self.idx(), 'a-s')
        self.assertEqual(str(ctx.exception), 'artifact raw/payload mismatch')
        r = self.score()
        self.assertTrue(r['layer1']['valid'])
        self.assertEqual(r['layer1']['artifact_representations'], {'required.json': 'legacy_pre_fingerprint_raw'})
        self.assertEqual(r['layer2'][0]['result'], 'supported')

    def test_content_difference_still_rejects_in_v2(self):
        row = self.write_tracked('required.json', '{"ok":true}')
        tampered = json.loads(self.raw(row).read_text('utf-8'))
        tampered['after_content'] = '{"ok":false}'
        self.raw(row).write_text(json.dumps(tampered, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
        self.finalize()
        self.assertFalse(self.score()['layer1']['valid'])
        self.assertEqual(self.score()['layer1']['reason'], 'artifact raw/payload mismatch')

    def test_placeholder_pattern_with_nonempty_raw_sha_still_rejects(self):
        row = self.write_tracked('required.json', '{"ok":true}')
        stale = json.loads(self.raw(row).read_text('utf-8'))
        stale.update(full_bytes=0, stored_bytes=0)
        self.raw(row).write_text(json.dumps(stale, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
        self.finalize()
        self.assertFalse(self.score()['layer1']['valid'])

    # ---- PII 表示域：listing（物理字节）必须与同域指纹比对，不与脱敏文本混用 ----

    def test_pii_domain_listing_uses_physical_fingerprint(self):
        redacted = '{"phone":"<PII_PHONE_1>"}'
        raw_text = '{"phone":"13900000000"}'
        rdata = redacted.encode('utf8')
        ddata = raw_text.encode('utf8')
        self.emit('file', 'file_write', 'agent', dict(
            path='required.json', op='create', after_content=redacted, before_content=None,
            full_bytes=len(rdata), stored_bytes=len(rdata), truncated=False,
            content_sha256=hashlib.sha256(rdata).hexdigest(),
            disk_sha256=hashlib.sha256(ddata).hexdigest(), disk_bytes=len(ddata)))
        self.listing['required.json'] = (len(ddata), hashlib.sha256(ddata).hexdigest())
        self.finalize()
        r = self.score()
        self.assertTrue(r['layer1']['valid'])

    def test_missing_disk_fingerprint_still_rejects_domain_mix(self):
        redacted = '{"phone":"<PII_PHONE_1>"}'
        raw_text = '{"phone":"13900000000"}'
        rdata = redacted.encode('utf8')
        ddata = raw_text.encode('utf8')
        self.emit('file', 'file_write', 'agent', dict(
            path='required.json', op='create', after_content=redacted, before_content=None,
            full_bytes=len(rdata), stored_bytes=len(rdata), truncated=False,
            content_sha256=hashlib.sha256(rdata).hexdigest()))
        self.listing['required.json'] = (len(ddata), hashlib.sha256(ddata).hexdigest())
        self.finalize()
        r = self.score()
        self.assertFalse(r['layer1']['valid'])
        self.assertEqual(r['layer1']['reason'], 'listing fingerprint mismatch')

    def test_self_inconsistent_payload_rejects_even_when_raw_matches(self):
        bad = '{"ok":true}'
        r = self.emit('file', 'file_write', 'agent', dict(path='required.json', op='create', after_content=bad,
            before_content=None, full_bytes=10, stored_bytes=10, truncated=False,
            content_sha256=EMPTY_SHA256))
        self.listing['required.json'] = (len(bad.encode('utf8')), hashlib.sha256(bad.encode('utf8')).hexdigest())
        self.finalize()
        row = self.score()
        self.assertFalse(row['layer1']['valid'])
        self.assertEqual(row['layer1']['reason'], 'artifact fingerprint mismatch')
        self.assertEqual(r['payload']['path'], 'required.json')

    # ---- 2. memory evidence is graded, T0 stays blocked, T1 is disclosed ----

    def test_declared_snapshot_unlocks_with_tier_and_review_flag(self):
        self.write_tracked('required.json', '{"ok":true}')
        self.finalize()
        v1 = self.score('scoped-v1')
        self.assertEqual(v1['layer1']['dependencies']['memory']['reason'], 'memory_scope_control_unverified')
        self.assertIs(v1['layer1']['dependencies']['memory']['sufficient'], False)
        self.assertNotIn('evidence_tier', v1['layer1']['dependencies']['memory'])
        self.assertEqual(v1['semantic_requests'], [])
        v2 = self.score()
        gate = v2['layer1']['dependencies']['memory']
        self.assertTrue(gate['sufficient'])
        self.assertEqual(gate['evidence_tier'], TIER_DECLARED)
        self.assertIs(gate['independent_instance_verified'], False)
        self.assertEqual(gate['reason'], 'memory_scope_control_unverified')
        self.assertEqual([q['check_id'] for q in v2['semantic_requests']], ['memory'])
        self.assertEqual(v2['layer3'][0]['evidence_tier'], TIER_DECLARED)
        self.assertEqual(v2['low_tier_memory_checks'], ['memory'])
        self.assertIn('low_tier_memory_evidence', v2['layer4']['reasons'])
        self.assertTrue(v2['layer4']['review_required'])

    def test_missing_memory_raw_stays_T0_and_does_not_become_a_pass(self):
        self.write_tracked('required.json', '{"ok":true}')
        self.finalize()
        self.raw(self.rows[3]).unlink()
        r = self.score()
        gate = r['layer1']['dependencies']['memory']
        self.assertIs(gate['sufficient'], False)
        self.assertEqual(gate['evidence_tier'], 'T0_absent_or_error')
        # Tiering must not turn an unobserved memory into a pass: still unassessable.
        self.assertEqual(r['task_outcome'], 'unassessable')
        self.assertIsNone(r['score'])
        self.assertEqual(r['layer2'][0]['result'], 'supported')

    def test_T0_does_not_hide_proven_file_failure(self):
        self.write_tracked('other.json', '{}')
        self.finalize()
        self.raw(self.rows[3]).unlink()
        r = self.score()
        self.assertEqual(r['layer1']['dependencies']['memory']['evidence_tier'], 'T0_absent_or_error')
        self.assertEqual(r['task_outcome'], 'fail')
        self.assertEqual(r['layer4']['violated_checks'], ['file'])

    def test_receipt_bound_snapshot_is_t2(self):
        self.write_tracked('required.json', '{"ok":true}')
        self.finalize()
        snap = self.rows[3]
        meta = file_scope(self.root, ['MEMORY.md'], 'test', now_iso(), now_iso())
        rec = partial_receipt(meta, run_id='run-v2', task_id='a-s', phase='after',
                              evidence_id=snap['id'], item_count=0)
        self.emit('system', 'message', 'env', {'action': 'observation_receipt', 'receipt': rec,
                  'scope_metadata': meta})
        r = self.score()
        gate = r['layer1']['dependencies']['memory']
        self.assertEqual(gate['evidence_tier'], TIER_BOUND)
        self.assertEqual(gate['reason'], 'memory_receipt_partial')
        self.assertNotIn('low_tier_memory_checks', r)

    def test_forged_complete_flags_reach_T2_without_claiming_verification(self):
        self.write_tracked('required.json', '{"ok":true}')
        self.finalize()
        snap = self.rows[3]
        meta = file_scope(self.root, ['MEMORY.md'], 'test', now_iso(), now_iso())
        rec = partial_receipt(meta, run_id='run-v2', task_id='a-s', phase='after',
                              evidence_id=snap['id'], item_count=0)
        rec.update(state='complete_empty', enumeration_complete=True, instance_verified=True,
                   control_passed=True, control_cleaned=True, control_refs=[self.rows[1]['id']], errors=[])
        self.emit('system', 'message', 'env', {'action': 'observation_receipt', 'receipt': rec,
                  'scope_metadata': meta})
        r = self.score()
        gate = r['layer1']['dependencies']['memory']
        self.assertEqual(gate['reason'], 'memory_control_verifier_unimplemented')
        self.assertIs(gate['independent_instance_verified'], False)
        self.assertEqual(gate['evidence_tier'], TIER_BOUND)

    def test_v1_memory_dependency_shape_is_unchanged(self):
        self.write_tracked('required.json', '{"ok":true}')
        self.finalize()
        for row in self.rows:
            if row['type'] == 'file_write':
                self.assertEqual(row['payload']['full_bytes'], row['payload']['stored_bytes'])
        gate = self.score('scoped-v1')['layer1']['dependencies']['memory']
        self.assertEqual(set(gate), {'sufficient', 'reason', 'full_capture_status'})


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
