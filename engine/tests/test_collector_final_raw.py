"""Final fallback raw must mirror final payload; explicit raw remains caller-owned."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError
from engine.collectors import EvidenceWriter, sha256_text, truncate_text
from simulate import simulate
import yaml


class FinalRawTests(unittest.TestCase):
    def raw_payload(self, root, row):
        lines = (root/row['raw_ref']['path']).read_text(encoding='utf8').splitlines()
        return json.loads(lines[row['raw_ref']['line'] - 1])

    def test_mutated_payload_is_final(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); writer = EvidenceWriter('final001', root)
            original = {'text': 'old', 'nested': {'n': 0}}
            def finish(row):
                row['payload']['text'] = 'new'
                row['payload']['nested']['n'] = 3
            row = writer.emit(task_id='t', source='file', type='file_write', actor='agent',
                              payload=original, postprocess=finish)
            self.assertEqual(self.raw_payload(root, row), row['payload'])
            self.assertEqual(original, {'text': 'old', 'nested': {'n': 0}})
            self.assertEqual(json.loads(writer.evidence_path.read_text(encoding='utf8')), row)

    def test_replaced_payload_is_final(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); writer = EvidenceWriter('final002', root)
            def finish(row):
                row['payload'] = {'replacement': [1, 2]}
            row = writer.emit(task_id='t', source='system', type='message', actor='env',
                              payload={'before': True}, postprocess=finish)
            self.assertEqual(self.raw_payload(root, row), {'replacement': [1, 2]})

    def test_explicit_raw_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); writer = EvidenceWriter('final003', root)
            ref = writer.write_raw_text('t', 'caller.txt', 'caller-owned\n')
            before = (root/ref['path']).read_bytes()
            row = writer.emit(task_id='t', source='system', type='message', actor='env',
                              payload={'x': 1}, raw_ref=ref,
                              postprocess=lambda r: r['payload'].update(x=2))
            self.assertEqual(row['raw_ref'], ref)
            self.assertEqual((root/ref['path']).read_bytes(), before)
            self.assertEqual(len(list((root/'raw').rglob('*.*'))), 1)

    def test_callback_sees_reserved_reference_before_file_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); writer = EvidenceWriter('final006', root)
            refs = []
            def finish(row):
                refs.append(dict(row['raw_ref']))
                self.assertFalse((root/row['raw_ref']['path']).exists())
                row['payload']['final'] = True
            row = writer.emit(task_id='t', source='system', type='message', actor='env',
                              payload={}, postprocess=finish)
            self.assertEqual(refs, [row['raw_ref']])
            self.assertEqual(self.raw_payload(root, row), {'final': True})

    def test_no_callback_and_empty_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); writer = EvidenceWriter('final004', root)
            row = writer.emit(task_id='t', source='system', type='message', actor='env', payload={})
            self.assertEqual(self.raw_payload(root, row), {})

    def test_failed_callback_emits_neither_evidence_nor_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); writer = EvidenceWriter('final005', root)
            def fail(row):
                raise RuntimeError('synthetic postprocess failure')
            with self.assertRaises(RuntimeError):
                writer.emit(task_id='t', source='system', type='message', actor='env',
                            payload={'x': 1}, postprocess=fail)
            self.assertFalse(writer.evidence_path.exists())
            self.assertFalse(list(root.rglob('evidence_ev_*.jsonl')))
            # Sequence reservation behavior is intentionally unchanged by this fix.
            self.assertEqual(writer.seq, 1)

    def test_runner_file_variants(self):
        # Exercise runner's actual nested emit_file_change postprocess, not a copy.
        for label, content, op, pii in [
            ('nonempty', 'abc\n', 'write', []),
            ('empty', '', 'write', []),
            ('delete', None, 'delete', []),
            ('unicode', '甲乙🙂\n', 'write', []),
            ('pii', 'SYNTHETICPRIVATEVALUE\n', 'write',
             [{'id': 'PII_TEST', 'kind': 'custom', 'value': 'SYNTHETICPRIVATEVALUE'}]),
            ('truncated', '甲🙂' * 20000, 'write', []),
        ]:
            with self.subTest(label=label), testtmp.TemporaryDirectory() as td:
                root = Path(td)
                sample = {'id': 'collector_final_raw', 'dimension': 'task_reuse',
                          'meta': {'suite_version': 'v0.2'}, 'anchors': [],
                          'score_policy': 'rule_only', 'expect': {'verdict': 'remembered'}}
                sample['pii'] = pii
                sample['fixture'] = [{'path': 'f.txt', 'content': 'prior\n'}]
                sample['script'] = [{'session': 's1', 'phase': 'probe', 'turns': [{'text': 'Write test artifact.'}]}]
                operation = {'op': op, 'path': 'f.txt'}
                if content is not None:
                    operation['content'] = content
                policy = {'policy_version': 1, 'tier': 'remember', 'on_missing_record': 'fail',
                          'defaults': {'reply_text': 'done'}, 'samples': [{'sample_id': sample['id'],
                          'turns': [{'match': {'session': 's1', 'turn': 1}, 'reply_text': 'done',
                                     'file_ops': [operation]}]}]}
                paper = root/'paper.jsonl'; paper.write_text(json.dumps(sample), encoding='utf8')
                pp = root/'policy.yaml'; pp.write_text(yaml.safe_dump(policy, allow_unicode=True), encoding='utf8')
                rd, _ = simulate(str(paper), str(pp), 'final-'+label, str(root/'runs'))
                rd = Path(rd)
                evidence = [json.loads(x) for x in (rd/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
                events = [r for r in evidence if r['type'] == 'file_write']
                self.assertEqual(len(events), 1)
                row = events[0]; payload = row['payload']
                self.assertEqual(self.raw_payload(rd, row), payload)
                full = (content or '').replace('SYNTHETICPRIVATEVALUE', '<PII_TEST>')
                stored, truncated, size, stored_size = truncate_text(full)
                self.assertEqual(payload['content_sha256'], sha256_text(full))
                self.assertEqual(payload['full_bytes'], size)
                self.assertEqual(payload['stored_bytes'], stored_size)
                self.assertEqual(payload['truncated'], truncated)
                self.assertEqual(payload['after_content'], None if op == 'delete' else stored)
                if pii:
                    self.assertTrue(row['pii_hits'])
                    self.assertNotIn('SYNTHETICPRIVATEVALUE', (rd/row['raw_ref']['path']).read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
