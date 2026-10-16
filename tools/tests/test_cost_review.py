"""Cost review is offline; all prices/token counts here are synthetic fixtures."""
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
sys.path[:0] = [str(ROOT), str(ROOT/'tools'), str(Path(__file__).parent)]
from paper_fixture import ensure_pilot_jsonl  # build/ 不在 VM 同步范围：缺了就重建并核对冻结 sha
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError
from scoring.fact_judge import request, fingerprint, canonical
from scoring.cost_review import inventory, scenario, wire_body
from scoring.audited_facts import AuditedFacts, read_journal
from review_judge_cost import main
from test_report_trace import fixture


class CostTests(unittest.TestCase):
    def setUp(self):
        self.req = request('fixture fact', {}, [dict(evidence_id='e', content={'text':'fact'})])
        self.requests = [dict(request=self.req, request_sha256=fingerprint(self.req), source={'case_id':'fixture'})]
        self.options = dict(model='fixture-model', base_url='https://example.invalid/v1', max_output_tokens=128)
        self.inv = inventory(self.requests, **self.options)
        self.quote = dict(format='okb-judge-price-quote-v1-draft', model='fixture-model',
            endpoint_sha256=self.inv['endpoint_sha256'], currency='USD', input_per_million='2',
            output_per_million='4', price_source='SYNTHETIC TEST ONLY', observed_on='2026-09-29')
        self.tokens = dict(format='okb-judge-token-counts-v1-draft', model='fixture-model',
            endpoint_sha256=self.inv['endpoint_sha256'], protocol=self.inv['protocol'],
            request_set_sha256=self.inv['request_set_sha256'], method='synthetic', source='fixture',
            records=[dict(wire_request_sha256=self.inv['requests'][0]['wire_request_sha256'], input_tokens=100)])

    def test_missing_prices_are_unknown_not_zero(self):
        result = scenario(self.inv)
        self.assertIsNone(result['total_cost']); self.assertEqual(result['status'], 'inputs_missing')

    def test_no_model_has_no_fake_wire_hash(self):
        inv = inventory(self.requests, model=None, base_url=None, max_output_tokens=128)
        self.assertIsNone(inv['requests'][0]['wire_request_sha256'])
        self.assertIsNone(inv['requests'][0]['wire_utf8_bytes'])
        self.assertGreater(inv['requests'][0]['messages_utf8_bytes'], 0)

    def test_money_exact_declared_scenario_not_hard_cap(self):
        result = scenario(self.inv, self.quote, self.tokens)
        self.assertEqual(result['total_cost'], '0.000712')
        self.assertEqual(result['output_tokens_if_every_call_hits_limit'], 128)
        self.assertFalse(result['hard_currency_cap'])

    def test_unknown_model_or_half_pricing_rejected(self):
        with self.assertRaises(ValueError): scenario(self.inv, self.quote)
        with self.assertRaises(ValueError): scenario(self.inv, tokens=self.tokens)
        with self.assertRaises(ValueError): inventory(self.requests, model='x', base_url=None, max_output_tokens=1)

    def test_duplicate_requests_rejected(self):
        with self.assertRaises(ValueError): inventory(self.requests*2, **self.options)

    def test_changed_request_rejected(self):
        self.requests[0]['request']['requirement'] = 'changed'
        with self.assertRaises(ValueError): inventory(self.requests, **self.options)

    def test_protocol_and_system_mismatch_rejected(self):
        for field in ('protocol', 'system_sha256'):
            with self.subTest(field=field):
                rows = copy.deepcopy(self.requests); rows[0]['request'][field] = 'wrong'
                rows[0]['request_sha256'] = fingerprint(rows[0]['request'])
                with self.assertRaises(ValueError): inventory(rows, **self.options)

    def test_output_limit_strict(self):
        for limit in (True, 0, -1, 32769, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                inventory(self.requests, **dict(self.options, max_output_tokens=limit))

    def test_endpoint_rejects_embedded_secrets(self):
        for base in ('http://example.invalid', 'https://u:p@example.invalid',
                     'https://example.invalid/?key=secret', 'https://example.invalid/#secret'):
            with self.subTest(base=base), self.assertRaises(ValueError):
                inventory(self.requests, **dict(self.options, base_url=base))

    def test_model_endpoint_set_and_wire_binding(self):
        for key in ('model', 'endpoint_sha256', 'protocol', 'request_set_sha256'):
            with self.subTest(key=key):
                tokens = copy.deepcopy(self.tokens); tokens[key] = 'wrong'
                with self.assertRaises(ValueError): scenario(self.inv, self.quote, tokens)
        self.tokens['records'][0]['wire_request_sha256'] = '0'*64
        with self.assertRaises(ValueError): scenario(self.inv, self.quote, self.tokens)

    def test_changed_output_limit_invalidates_tokens(self):
        inv = inventory(self.requests, **dict(self.options, max_output_tokens=129))
        with self.assertRaises(ValueError): scenario(inv, self.quote, self.tokens)

    def test_token_coverage_duplicate_bool_and_negative_rejected(self):
        cases = [[], self.tokens['records']*2]
        for n in (True, -1, 0, 1.2, 10**9+1):
            cases.append([dict(self.tokens['records'][0], input_tokens=n)])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                scenario(self.inv, self.quote, dict(self.tokens, records=rows))

    def test_bad_quote_fields_price_date_currency_provenance(self):
        for k, v in [('extra', 1), ('input_per_million', 'NaN'), ('output_per_million', '-1'),
                     ('input_per_million', '1e999'), ('input_per_million', '1e-999'), ('input_per_million', '0e999'),
                     ('input_per_million', 2), ('currency', 'usd'), ('price_source', ''),
                     ('observed_on', '2026-02-30'), ('model', 'other')]:
            with self.subTest(k=k, v=v), self.assertRaises(ValueError):
                scenario(self.inv, dict(self.quote, **{k:v}), self.tokens)

    def test_wire_matches_audited_transport_and_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            client = AuditedFacts(journal_path=Path(temp)/'journal.jsonl',
                budget=dict(currency='USD', max_total_cost='1', reserve_per_request='0.1',
                    input_per_million='2', output_per_million='4', price_source='SYNTHETIC'),
                api_key='fixture', max_calls=1, **self.options)
            captured = []
            def exchange(body):
                captured.append(body); raise TimeoutError('fixture; no HTTP')
            with patch.object(client, '_exchange', side_effect=exchange):
                with self.assertRaises(ValueError): client.judge(self.req)
            self.assertEqual(captured, [wire_body(self.req, 'fixture-model', 128)])
            self.assertEqual(len(canonical(captured[0]).encode()), self.inv['requests'][0]['wire_utf8_bytes'])
            event = next(iter(read_journal(client.path)['pending'].values()))
            self.assertEqual(event['wire_request_sha256'], self.inv['requests'][0]['wire_request_sha256'])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = testtmp.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def call(self, extra=(), name='out'):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['--max-output-tokens', '128', '--out-dir', str(self.root/name), *extra])

    def test_real_development_corpus_inventory_and_no_labels_in_requests(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.call(), 0)
        review = json.loads((self.root/'out/review.json').read_text(encoding='utf8'))
        self.assertEqual(review['calls_if_all_dispatched_once'], 33)
        self.assertFalse(review['execution_authorized']); self.assertIsNone(review['estimate']['total_cost'])
        requests = json.loads((self.root/'out/requests.json').read_text(encoding='utf8'))
        for row in requests:
            self.assertNotIn('expected', row['request']); self.assertNotIn('category', row['request'])
        import hashlib
        complete = json.loads((self.root/'out/complete.json').read_text())
        for name, digest in complete.items():
            self.assertEqual(digest, hashlib.sha256((self.root/'out'/name).read_bytes()).hexdigest())

    def test_existing_output_and_incomplete_history_rejected(self):
        self.assertEqual(self.call(), 0); self.assertEqual(self.call(), 2)
        self.assertEqual(self.call(['--score-dir', 'missing'], name='partial'), 2)
        self.assertFalse((self.root/'partial').exists())

    def test_cli_priced_scenario_with_bound_fixture_inputs(self):
        options = ['--model', 'fixture-model', '--base-url', 'https://example.invalid/v1']
        self.assertEqual(self.call(options), 0)
        inv = json.loads((self.root/'out/review.json').read_text(encoding='utf8'))
        quote = dict(format='okb-judge-price-quote-v1-draft', model=inv['model'],
            endpoint_sha256=inv['endpoint_sha256'], currency='USD', input_per_million='2',
            output_per_million='4', price_source='SYNTHETIC TEST ONLY', observed_on='2026-09-29')
        tokens = dict(format='okb-judge-token-counts-v1-draft', model=inv['model'],
            endpoint_sha256=inv['endpoint_sha256'], protocol=inv['protocol'],
            request_set_sha256=inv['request_set_sha256'], method='synthetic', source='fixture',
            records=[dict(wire_request_sha256=r['wire_request_sha256'], input_tokens=100) for r in inv['requests']])
        for name, obj in [('quote', quote), ('tokens', tokens)]:
            (self.root/(name+'.json')).write_text(json.dumps(obj))
        self.assertEqual(self.call(options+['--quote', str(self.root/'quote.json'),
            '--token-counts', str(self.root/'tokens.json')], name='priced'), 0)
        report = json.loads((self.root/'priced/review.json').read_text(encoding='utf8'))
        self.assertEqual(report['estimate']['total_cost'], '0.023496')
        self.assertFalse(report['execution_authorized'])

    def test_historical_source_rejection_remains_visible(self):
        history = ROOT.parent/'_review/four-layer/s1-s2-batch2-scoped-d1'
        if not history.is_dir():
            self.skipTest('local frozen historical bundle not distributed')
        args = ['--score-dir', str(history), '--run-dir', str(ROOT/'runs/run_v02_d1'),
                '--paper', ensure_pilot_jsonl(), '--plan', str(ROOT/'samples/four-layer-pilot.v1.json')]
        self.assertEqual(self.call(args), 0)
        result = json.loads((self.root/'out/review.json').read_text(encoding='utf8'))
        self.assertEqual(result['history_source_invalid'], 4)
        self.assertEqual(result['calls_if_all_dispatched_once'], 33)
        self.assertTrue(all(t['layer1']['reason'] == 'artifact raw/payload mismatch' for t in result['historical_tasks']))

    def test_duplicate_json_keys_rejected_without_output(self):
        corpus = self.root/'bad.json'; corpus.write_text('{"format":1,"format":2}')
        self.assertEqual(self.call(['--corpus', str(corpus)]), 2)
        self.assertFalse((self.root/'out').exists())

    def test_input_drift_prevents_completion(self):
        import review_judge_cost
        corpus = self.root/'copy.json'
        corpus.write_bytes((ROOT/'samples/semantic-robustness.v1.json').read_bytes())
        original = review_judge_cost.scenario
        def drift(*args):
            corpus.write_bytes(corpus.read_bytes()+b'\n'); return original(*args)
        with patch.object(review_judge_cost, 'scenario', side_effect=drift):
            self.assertEqual(self.call(['--corpus', str(corpus)]), 2)
        self.assertFalse((self.root/'out/complete.json').exists())

    def test_real_projection_imports_pending_and_lists_memory_block(self):
        with contextlib.redirect_stdout(io.StringIO()): scored, run, paper, plan = fixture(self.root)
        args = ['--score-dir', str(self.root/'prepare'), '--run-dir', str(run), '--paper', str(paper), '--plan', str(plan)]
        self.assertEqual(self.call(args), 0)
        result = json.loads((self.root/'out/review.json').read_text(encoding='utf8'))
        self.assertEqual(result['calls_if_all_dispatched_once'], 36)
        self.assertEqual(result['historical_tasks'][0]['pending_requests'], 3)
        self.assertEqual(result['historical_tasks'][0]['blocked_semantic_checks'][0]['id'], 'memory')
        args[1] = str(scored)
        self.assertEqual(self.call(args, name='replay-rejected'), 2)

    def test_rehashed_tampered_prepare_request_rejected(self):
        with contextlib.redirect_stdout(io.StringIO()): _, run, paper, plan = fixture(self.root)
        folder = self.root/'prepare'
        rows = json.loads((folder/'results.json').read_text(encoding='utf8'))
        rows[0]['semantic_requests'][0]['request']['requirement'] = 'tampered'
        (folder/'results.json').write_text(json.dumps(rows), encoding='utf8')
        import hashlib
        complete = json.loads((folder/'complete.json').read_text())
        complete['results_sha256'] = hashlib.sha256((folder/'results.json').read_bytes()).hexdigest()
        (folder/'complete.json').write_text(json.dumps(complete))
        self.assertEqual(self.call(['--score-dir', str(folder), '--run-dir', str(run),
            '--paper', str(paper), '--plan', str(plan)]), 2)


if __name__ == '__main__':
    unittest.main()
