#!/usr/bin/env python3
"""Real prompt/cache paths, fake transport only; no credentials or network.

The frozen local mock evidence is used. All batch files live in temp dirs.
"""
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
from paper_fixture import ensure_pilot_jsonl  # build/ 不在 VM 同步范围：缺了就重建并核对冻结 sha
import judge_drift as drift
from scoring.judge_client import OpenAICompatJudge as RealJudge, JudgeError, ALLOWED


class FakeJudge(RealJudge):
    remote_calls = 0
    fail = False
    verdict = 'remembered'

    def __init__(self, repo_root, model=None, cache_dir=None, **kwargs):
        self.model, self.cache_dir = model, cache_dir
        self.allowed = ALLOWED
        self.max_calls, self.calls, self.cache_hits, self.total_tokens = 300, 0, 0, 0
        self._cache = self._load_cache()

    def _call(self, prompt):
        type(self).remote_calls += 1
        if type(self).fail:
            raise JudgeError('synthetic failure; must not leak sensitive error text')
        return {'verdict': type(self).verdict, 'reason': 'fake offline transport'}


class DriftBatchTests(unittest.TestCase):
    def setUp(self):
        if not (ROOT / 'runs').is_dir():
            # 漂移批要拿历史 runs 做对照，runs/ 不在 VM 同步清单里 → 全部用例如实 skip
            # （上一版我把守卫只写在单个用例里，其余 16 条在 VM 等价树上照样炸 ERROR）
            self.skipTest("runs/ 不在 deploy/sync-to-vm.ps1 的同步范围内，漂移批需要历史 runs 证据；"
                          "在有 runs/ 的开发机上会真跑，这里如实跳过而不是假红")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batches = self.root / 'batches'
        self.cases = self.root / 'cases.json'
        original = json.loads((ROOT / 'samples/judge-anchor-cases.v0.1.json').read_text(encoding='utf-8'))
        original['cases'] = [original['cases'][0]]
        self.cases.write_text(json.dumps(original), encoding='utf-8')
        FakeJudge.remote_calls, FakeJudge.fail, FakeJudge.verdict = 0, False, 'remembered'

    def invoke(self, *args):
        with patch.object(drift, 'OpenAICompatJudge', FakeJudge), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return drift.main([*args, '--batch-root', str(self.batches), '--cases', str(self.cases)])
            except SystemExit as e:
                return e.code

    def collect(self, batch='d0', *args):
        return self.invoke('--collect', '--batch-id', batch, '--model', 'offline-test-model', '--max-calls', '1', *args)

    def replay(self, batch='d0', *args):
        return self.invoke('--replay', '--batch-id', batch, '--report', str(self.root / (batch + '-replay.json')), *args)

    def result(self, batch='d0'):
        return json.loads((self.batches / batch / 'result.json').read_text(encoding='utf-8'))

    def test_existing_client_reuses_cache_without_date_boundary(self):
        # Reproduce the original mechanism: same inputs + shared cache => no new observation.
        prepared, _ = drift.prepare(self.cases, Path(ensure_pilot_jsonl()), ROOT / 'runs')
        judge = FakeJudge(str(ROOT), model='offline-test-model', cache_dir=str(self.root / 'legacy-cache'))
        drift.evaluate_cases(judge, prepared)
        again = FakeJudge(str(ROOT), model='offline-test-model', cache_dir=str(self.root / 'legacy-cache'))
        drift.evaluate_cases(again, prepared)
        self.assertEqual(FakeJudge.remote_calls, 1)
        self.assertEqual(again.calls, 0)
        self.assertEqual(again.cache_hits, 1)

    def test_full_fourteen_case_pipeline_offline(self):
        self.cases.write_bytes((ROOT / 'samples/judge-anchor-cases.v0.1.json').read_bytes())
        rc = self.invoke('--collect', '--batch-id', 'full', '--model', 'offline-test-model', '--max-calls', '14')
        self.assertEqual(rc, 1)  # deliberately always remembered, not all bands satisfied
        r = self.result('full')
        self.assertTrue(r['complete'])
        self.assertEqual(r['successful_calls'], 14)
        self.assertEqual(r['cache_hits'], 0)
        self.assertEqual(sum(x['mode'] == 'shadow' for x in r['records']), 10)
        self.assertEqual(sum(x['eligible_for_stability'] for x in r['records']), 9)
        self.assertEqual(self.replay('full'), 1)
        self.assertEqual(FakeJudge.remote_calls, 14)
        replay = json.loads((self.root / 'full-replay.json').read_text(encoding='utf-8'))
        self.assertTrue(replay['complete'])
        self.assertEqual(replay['cache_hits'], 14)
        self.assertEqual(replay['replay_diffs'], [])

    def test_collect_calls_transport(self):
        self.assertEqual(self.collect(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)
        r = self.result()
        self.assertEqual(r['successful_calls'], 1)
        self.assertEqual(r['cache_hits'], 0)
        self.assertTrue(r['complete'])

    def test_new_batch_cannot_reuse_previous_cache(self):
        self.assertEqual(self.collect(), 0)
        old = (self.batches / 'd0/judge_cache.jsonl').read_bytes()
        self.assertEqual(self.collect('d1'), 0)
        self.assertEqual(FakeJudge.remote_calls, 2)
        self.assertEqual(old, (self.batches / 'd0/judge_cache.jsonl').read_bytes())

    def test_reused_batch_rejected_before_call(self):
        self.assertEqual(self.collect(), 0)
        self.assertNotEqual(self.collect(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_replay_no_credentials_or_transport(self):
        self.assertEqual(self.collect(), 0)
        cache = (self.batches / 'd0/judge_cache.jsonl').read_bytes()
        with patch.object(FakeJudge, '__init__', side_effect=AssertionError('must not read credentials')):
            self.assertEqual(self.replay(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)
        self.assertEqual(cache, (self.batches / 'd0/judge_cache.jsonl').read_bytes())

    def test_tampered_cache_cannot_pass_replay(self):
        self.assertEqual(self.collect(), 0)
        (self.batches / 'd0/judge_cache.jsonl').write_text('', encoding='utf-8')
        self.assertNotEqual(self.replay(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_failure_retained_not_zero_drift(self):
        FakeJudge.fail = True
        self.assertNotEqual(self.collect(), 0)
        r = self.result()
        self.assertFalse(r['complete'])
        self.assertEqual(r['unavailable'], 1)
        self.assertEqual(r['attempted_requests'], 1)
        self.assertEqual(r['successful_calls'], 0)
        self.assertNotIn('sensitive error text', json.dumps(r))
        self.assertNotEqual(self.replay(), 0)

    def test_model_change_replay_rejected(self):
        self.assertEqual(self.collect(), 0)
        self.assertNotEqual(self.replay('d0', '--model', 'another-model'), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_case_input_change_rejected(self):
        self.assertEqual(self.collect(), 0)
        data = json.loads(self.cases.read_text(encoding='utf-8'))
        data['cases'][0]['expected'] = ['omitted']
        self.cases.write_text(json.dumps(data), encoding='utf-8')
        self.assertNotEqual(self.replay(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_cache_miss_cannot_call_even_if_digest_resealed(self):
        self.assertEqual(self.collect(), 0)
        import hashlib
        cache = self.batches / 'd0/judge_cache.jsonl'
        cache.write_text('', encoding='utf-8')
        r = self.result()
        r['cache_sha256'] = hashlib.sha256(b'').hexdigest()
        (self.batches / 'd0/result.json').write_text(json.dumps(r), encoding='utf-8')
        self.assertNotEqual(self.replay(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_baseline_comparison_reports_transition(self):
        self.assertEqual(self.collect(), 0)
        FakeJudge.verdict = 'omitted'
        self.assertNotEqual(self.collect('d1', '--baseline', 'd0'), 0)  # out of expected band
        r = self.result('d1')
        self.assertTrue(r['complete'])  # acquired, but scientifically out of band
        self.assertEqual(r['comparison']['changed'], 1)
        self.assertEqual(r['comparison']['eligible_changed'], 1)
        self.assertEqual(r['comparison']['kind'], 'same_day_new_collection')

    def test_budget_smaller_than_cases_rejected(self):
        rc = self.invoke('--collect', '--batch-id', 'd0', '--model', 'offline-test-model', '--max-calls', '0')
        self.assertNotEqual(rc, 0)
        self.assertEqual(FakeJudge.remote_calls, 0)
        self.assertFalse((self.batches / 'd0').exists())

    def test_missing_case_evidence_rejected_before_collection(self):
        data = json.loads(self.cases.read_text(encoding='utf-8'))
        data['cases'][0]['sample_id'] = 'does-not-exist'
        self.cases.write_text(json.dumps(data), encoding='utf-8')
        self.assertNotEqual(self.collect(), 0)
        self.assertEqual(FakeJudge.remote_calls, 0)

    def test_bad_batch_id_rejected(self):
        self.assertNotEqual(self.collect('../escape'), 0)
        self.assertEqual(FakeJudge.remote_calls, 0)

    def test_report_never_overwrites_source(self):
        self.assertEqual(self.collect(), 0)
        before = (self.batches / 'd0/result.json').read_bytes()
        rc = self.invoke('--replay', '--batch-id', 'd0', '--report', str(self.batches / 'd0/result.json'))
        self.assertNotEqual(rc, 0)
        self.assertEqual(before, (self.batches / 'd0/result.json').read_bytes())

    def test_result_cache_disagreement_cannot_be_baseline(self):
        self.assertEqual(self.collect(), 0)
        r = self.result()
        r['records'][0]['verdict'] = 'omitted'
        (self.batches / 'd0/result.json').write_text(json.dumps(r), encoding='utf-8')
        self.assertNotEqual(self.collect('d1', '--baseline', 'd0'), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)
        self.assertFalse((self.batches / 'd1').exists())

    def test_failing_baseline_does_not_get_zero_drift_comparison(self):
        self.assertEqual(self.collect(), 0)
        FakeJudge.fail = True
        self.assertNotEqual(self.collect('d1', '--baseline', 'd0'), 0)
        self.assertIsNone(self.result('d1')['comparison'])
        self.assertFalse(self.result('d1')['complete'])

    def test_duplicate_cases_rejected_before_call(self):
        data = json.loads(self.cases.read_text(encoding='utf-8'))
        data['cases'].append(dict(data['cases'][0], case_id='different-id-same-task'))
        self.cases.write_text(json.dumps(data), encoding='utf-8')
        self.assertNotEqual(self.invoke('--collect', '--batch-id', 'dup', '--model', 'm', '--max-calls', '2'), 0)
        self.assertEqual(FakeJudge.remote_calls, 0)

    def test_incompatible_baseline_rejected_before_new_batch(self):
        self.assertEqual(self.collect(), 0)
        rc = self.invoke('--collect', '--batch-id', 'd1', '--model', 'other', '--max-calls', '1', '--baseline', 'd0')
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.batches / 'd1').exists())
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_failed_requests_consume_logical_budget(self):
        self.cases.write_bytes((ROOT / 'samples/judge-anchor-cases.v0.1.json').read_bytes())
        FakeJudge.fail = True
        self.assertNotEqual(self.invoke('--collect', '--batch-id', 'failed14', '--model', 'm', '--max-calls', '14'), 0)
        r = self.result('failed14')
        self.assertEqual(r['attempted_requests'], 14)
        self.assertEqual(r['successful_calls'], 0)
        self.assertEqual(r['http_attempt_upper_bound'], 42)
        self.assertEqual(r['unavailable'], 14)
        self.assertIsNone(r['comparison'])

    def test_initialization_failure_recorded_without_secrets(self):
        with patch.object(FakeJudge, '__init__', side_effect=JudgeError('secret must not persist')):
            self.assertNotEqual(self.collect(), 0)
        result = self.result()
        self.assertFalse(result['complete'])
        self.assertEqual(result['attempted_requests'], 0)
        self.assertNotIn('secret must not persist', json.dumps(result))

    def test_cross_day_comparison_keeps_provisional_out_of_eligible(self):
        previous = {'records': [{'case_id': 'x', 'verdict': 'remembered'}]}
        rows = [{'case_id': 'x', 'status': 'ok', 'verdict': 'omitted', 'eligible_for_stability': False}]
        c = drift.compare_batches(previous, rows, '2026-09-29T00:00:00+00:00', '2026-09-28T23:59:00+00:00')
        self.assertEqual(c['kind'], 'cross_day_new_collection')
        self.assertEqual(c['changed'], 1)
        self.assertEqual(c['eligible_changed'], 0)

    def test_replay_report_cannot_be_reused(self):
        self.assertEqual(self.collect(), 0)
        self.assertEqual(self.replay(), 0)
        self.assertNotEqual(self.replay(), 0)
        self.assertEqual(FakeJudge.remote_calls, 1)

    def test_legacy_same_day_requires_explicit_batch(self):
        self.assertNotEqual(self.invoke('--same-day'), 0)
        self.assertEqual(FakeJudge.remote_calls, 0)

    def test_bare_cli_does_not_trigger_network(self):
        self.assertNotEqual(self.invoke(), 0)
        self.assertEqual(FakeJudge.remote_calls, 0)


if __name__ == '__main__':
    unittest.main()
