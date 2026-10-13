"""Offline synthetic fixtures only; no quotes, approvals or network calls."""
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
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError
from scoring.audited_facts import AuditedFacts, read_journal
from scoring.cost_review import inventory, wire_body
from scoring.cost_runtime import runtime_contract, validate_runtime_contract
from scoring.fact_judge import FactError, canonical, fingerprint, request


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'journal.jsonl'
        self.req = request('fixture fact', {}, [dict(evidence_id='e', content={'text':'fact'})])
        self.rows = [dict(request=self.req, request_sha256=fingerprint(self.req), source={'case_id':'fixture'})]
        self.options = dict(model='fixture-model', base_url='https://example.invalid/v1', max_output_tokens=128)
        self.inv = inventory(self.rows, **self.options)
        self.quote = dict(format='okb-judge-price-quote-v1-draft', model='fixture-model',
            endpoint_sha256=self.inv['endpoint_sha256'], currency='USD', input_per_million='2',
            output_per_million='4', price_source='SYNTHETIC TEST ONLY', observed_on='2026-09-30')
        self.tokens = dict(format='okb-judge-token-counts-v1-draft', model='fixture-model',
            endpoint_sha256=self.inv['endpoint_sha256'], protocol=self.inv['protocol'],
            request_set_sha256=self.inv['request_set_sha256'], method='synthetic', source='fixture',
            records=[dict(wire_request_sha256=self.inv['requests'][0]['wire_request_sha256'], input_tokens=100)])
        self.budget = dict(currency='USD', input_per_million='2', output_per_million='4',
            price_source='SYNTHETIC TEST ONLY', max_total_cost='0.01', reserve_per_request='0.001')
        self.contract = runtime_contract(self.inv, self.quote, self.tokens, self.budget, 1)

    def validate(self, contract=None, rows=None, **changes):
        options = dict(self.options, budget=self.budget, max_calls=1); options.update(changes)
        return validate_runtime_contract(contract or self.contract, self.rows if rows is None else rows, **options)

    def test_binding_never_authorizes_or_guarantees_hard_cap(self):
        self.assertEqual(self.validate()['contract_sha256'], self.contract['contract_sha256'])
        self.assertFalse(self.contract['execution_authorized']); self.assertFalse(self.contract['hard_monetary_cap'])
        self.assertEqual(self.contract['scenario_total_cost'], '0.000712')

    def test_changed_runtime_parameters_rejected(self):
        for field, value in [('model', 'other'), ('base_url', 'https://other.invalid/v1'),
                             ('max_output_tokens', 129), ('max_calls', 2), ('max_calls', True)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate(**{field:value})

    def test_request_subset_extra_duplicate_or_source_drift_rejected(self):
        changed = copy.deepcopy(self.rows); changed[0]['source'] = {'case_id':'different'}
        for rows in ([], self.rows*2, changed):
            with self.subTest(rows=rows), self.assertRaises(ValueError): self.validate(rows=rows)
        changed = copy.deepcopy(self.rows); changed[0]['request']['requirement'] = 'changed'
        changed[0]['request_sha256'] = fingerprint(changed[0]['request'])
        with self.assertRaises(ValueError): self.validate(rows=changed)

    def test_contract_tampering_and_rehash_do_not_bypass_reconstruction(self):
        for field, value in [('execution_authorized', True), ('hard_monetary_cap', True), ('format', 'other'),
                             ('reserved_batch_cost', '0'), ('extra', 1)]:
            changed = copy.deepcopy(self.contract); changed[field] = value
            changed['contract_sha256'] = fingerprint({key:val for key,val in changed.items() if key != 'contract_sha256'})
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate(contract=changed)

    def test_price_currency_provenance_and_reservation_must_match(self):
        for field, value in [('currency', 'CNY'), ('input_per_million', '3'), ('price_source', 'other'),
                             ('reserve_per_request', '0.0001'), ('max_total_cost', '0.0009'),
                             ('max_total_cost', '1e999'), ('reserve_per_request', '1e-999')]:
            changed = dict(self.budget, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime_contract(self.inv, self.quote, self.tokens, changed, 1)

    def test_full_batch_reservations_not_just_scenario_total(self):
        rows = self.rows + [dict(request=request('second', {}, self.req['observations']), source={'case_id':'second'})]
        rows[1]['request_sha256'] = fingerprint(rows[1]['request'])
        inv = inventory(rows, **self.options)
        tokens = dict(self.tokens, request_set_sha256=inv['request_set_sha256'],
            records=[dict(wire_request_sha256=row['wire_request_sha256'], input_tokens=100) for row in inv['requests']])
        with self.assertRaises(ValueError):
            runtime_contract(inv, self.quote, tokens, dict(self.budget, max_total_cost='0.0015'), 2)

    def test_invalid_contract_creates_no_journal(self):
        with self.assertRaises(ValueError):
            AuditedFacts(journal_path=self.path, budget=self.budget, max_calls=2, api_key='fixture',
                runtime_contract=self.contract, reviewed_requests=self.rows, **self.options)
        self.assertFalse(self.path.exists())

    def test_unknown_cost_inputs_cannot_mint_contract(self):
        for quote, tokens in ((None, None), (self.quote, None), (None, self.tokens)):
            with self.subTest(quote=quote, tokens=tokens), self.assertRaises(ValueError):
                runtime_contract(self.inv, quote, tokens, self.budget, 1)

    def test_dispatch_uses_reviewed_wire_body_and_journal_binding(self):
        client = AuditedFacts(journal_path=self.path, budget=self.budget, max_calls=1, api_key='fixture',
            runtime_contract=self.contract, reviewed_requests=self.rows, **self.options)
        with patch.object(client, '_exchange', side_effect=RuntimeError('synthetic interruption')) as exchange:
            with self.assertRaises(FactError): client.judge(self.req)
            exchange.assert_called_once_with(wire_body(self.req, 'fixture-model', 128))
            with self.assertRaises(FactError): client.judge(self.req)
            self.assertEqual(exchange.call_count, 1)
        recovered = read_journal(self.path)
        self.assertEqual(recovered['events'][0]['runtime_contract_sha256'], self.contract['contract_sha256'])

    def test_unreviewed_request_never_reserves_or_sends(self):
        client = AuditedFacts(journal_path=self.path, budget=self.budget, max_calls=1, api_key='fixture',
            runtime_contract=self.contract, reviewed_requests=self.rows, **self.options)
        with patch.object(client, '_exchange') as exchange:
            with self.assertRaises(FactError): client.judge(dict(self.req, requirement='unreviewed'))
            exchange.assert_not_called()
        self.assertEqual(client.calls, 0); self.assertEqual(read_journal(self.path)['pending'], {})

    def test_bound_model_mismatch_keeps_usage_but_cost_unknown_and_halts(self):
        for model in (None, 'other-model'):
            path = Path(self.temp.name)/('missing.jsonl' if model is None else 'other.jsonl')
            client = AuditedFacts(journal_path=path, budget=self.budget, max_calls=1, api_key='fixture',
                runtime_contract=self.contract, reviewed_requests=self.rows, **self.options)
            envelope = dict(model=model, usage=dict(prompt_tokens=100,completion_tokens=20))
            with patch.object(client, '_exchange', return_value=envelope):
                with self.assertRaises(FactError): client.judge(self.req)
            terminal = next(iter(read_journal(path)['terminal'].values()))
            self.assertEqual(terminal['usage'], envelope['usage']); self.assertIsNone(terminal['estimated_cost'])
            self.assertTrue(client.halted); self.assertEqual(client.unmeasured, 1)

    def test_live_cli_missing_contract_denied_before_output_or_http(self):
        from semantic_robustness import main
        from score_four_layer import main as score
        budget_path = Path(self.temp.name)/'budget.json'; budget_path.write_text(canonical(self.budget), encoding='utf8')
        out = Path(self.temp.name)/'denied'
        common = ['--out-dir',str(out),'--live','--authorize-paid-calls','--model','fixture-model',
            '--base-url',self.options['base_url'],'--max-calls','1','--max-output-tokens','128','--budget-file',str(budget_path)]
        with patch('urllib.request.build_opener') as http, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(common), 2)
            self.assertEqual(score(common+['--paper','missing','--plan','missing','--run-dir','missing']), 2)
            http.assert_not_called()
        self.assertFalse(out.exists())

    def test_scoped_score_preflight_matches_reviewed_historical_requests(self):
        sys.path.insert(0, str(Path(__file__).parent))
        from test_report_trace import fixture
        from review_judge_cost import main as review
        from score_four_layer import main as score
        with testtmp.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            _, run, paper, plan = fixture(root)
            unpriced = root/'unpriced'
            history = ['--history-only','--score-dir',str(root/'prepare'),'--run-dir',str(run),'--paper',str(paper),'--plan',str(plan)]
            self.assertEqual(review(history+['--out-dir',str(unpriced),'--model','fixture-model',
                '--base-url',self.options['base_url'],'--max-output-tokens','128']), 0)
            report = json.loads((unpriced/'review.json').read_text(encoding='utf8'))
            historical = [row for row in json.loads((unpriced/'requests.json').read_text(encoding='utf8'))
                          if row['source']['kind'] == 'historical_prepare']
            inv = inventory(historical, **self.options)
            tokens = dict(self.tokens, request_set_sha256=inv['request_set_sha256'],
                records=[dict(wire_request_sha256=row['wire_request_sha256'],input_tokens=100) for row in inv['requests']])
            contract = runtime_contract(inv, self.quote, tokens, self.budget, len(historical))
            contract_path = root/'contract.json'; contract_path.write_text(canonical(contract), encoding='utf8')
            budget_path = root/'budget.json'; budget_path.write_text(canonical(self.budget), encoding='utf8')
            args = ['--paper',str(paper),'--plan',str(plan),'--run-dir',str(run),'--outcome-version','v2',
                '--evidence-policy','scoped-v1','--live','--authorize-paid-calls','--model','fixture-model',
                '--base-url',self.options['base_url'],'--max-calls',str(len(historical)),'--max-output-tokens','128',
                '--budget-file',str(budget_path),'--runtime-contract-file',str(contract_path)]
            with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}), patch.object(AuditedFacts,'_exchange',side_effect=RuntimeError('fixture interruption')) as http:
                from test_cost_group import group_cli_args
                args += group_cli_args(root, contract)
                self.assertEqual(score(args+['--out-dir',str(root/'live')]), 0)
                self.assertEqual(http.call_count, 1)
            self.assertEqual(read_journal(root/'live/call-journal.jsonl')['batch']['runtime_contract_sha256'], contract['contract_sha256'])
            self.assertEqual(report['calls_if_all_dispatched_once'], len(historical))


if __name__ == '__main__':
    unittest.main()
