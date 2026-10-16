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
sys.path.insert(0, str(ROOT/'tools'))
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError

from scoring.audited_facts import AuditedFacts, estimate_cost, read_journal, validate_budget
from scoring.cache_billing import BUDGET_VERSION, PROFILE, QUOTE_VERSION, WIRE_PROFILE, response_cost
from scoring.cost_group import CostGroup, cost_group, initialize_ledger
from scoring.cost_review import inventory, scenario, wire_body
from scoring.cost_runtime import runtime_contract, validate_runtime_contract
from scoring.fact_judge import FactError, canonical, fingerprint, request


class CacheBillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.req = request('synthetic fact', {}, [dict(evidence_id='e1',content=dict(text='fixture'))])
        self.rows = [dict(request=self.req,request_sha256=fingerprint(self.req),source=dict(case_id='fixture'))]
        self.options = dict(model='gpt-6.1-sol', base_url='https://example.invalid/v1', max_output_tokens=128,
            wire_profile=WIRE_PROFILE, reasoning_effort='low')
        self.inv = inventory(self.rows, **self.options)
        self.rates = dict(input_uncached_per_million='2',input_cache_hit_per_million='0.1',
            input_cache_write_per_million='2.5',output_per_million='10')
        self.conditions = dict(service_tier='default',min_input_tokens=1,max_input_tokens=272000)
        self.quote = dict(format=QUOTE_VERSION,billing_profile=PROFILE,model=self.options['model'],
            endpoint_sha256=self.inv['endpoint_sha256'],currency='USD',rates=self.rates,
            pricing_conditions=self.conditions,price_source='SYNTHETIC TEST ONLY',observed_on='2026-09-30')
        self.tokens = dict(format='okb-judge-token-counts-v1-draft',model=self.options['model'],
            endpoint_sha256=self.inv['endpoint_sha256'],protocol=self.inv['protocol'],
            request_set_sha256=self.inv['request_set_sha256'],method='synthetic',source='fixture',
            records=[dict(wire_request_sha256=self.inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        self.budget = dict(format=BUDGET_VERSION,billing_profile=PROFILE,currency='USD',max_total_cost='0.01',
            reserve_per_request='0.002',rates=self.rates,pricing_conditions=self.conditions,price_source='SYNTHETIC TEST ONLY')
        self.contract = runtime_contract(self.inv,self.quote,self.tokens,self.budget,1)

    def envelope(self):
        answer = dict(result='supported',rationale='synthetic fixture',citations=[dict(evidence_id='e1',pointer='/text',quote='fixture')])
        return dict(model='gpt-6.1-sol',service_tier='default',
            usage=dict(prompt_tokens=100,completion_tokens=30,total_tokens=130,
                prompt_tokens_details=dict(cached_tokens=20,cache_write_tokens=30),
                completion_tokens_details=dict(reasoning_tokens=10)),
            choices=[dict(finish_reason='stop',message=dict(content=canonical(answer)))])

    def client(self, *, scope=None, group=None, name='calls.jsonl'):
        return AuditedFacts(journal_path=self.root/name,budget=self.budget,max_calls=1,api_key='synthetic',
            runtime_contract=self.contract,reviewed_requests=self.rows,cost_group=group,cost_scope=scope,**self.options)

    def group(self):
        policy = cost_group([('left',self.contract),('right',self.contract)],group_id='synthetic',
            currency='USD',max_total_cost='0.004',ledger_path=self.root/'group.sqlite3')
        initialize_ledger(policy)
        return CostGroup(policy)

    def cost(self, usage=None, **changes):
        envelope = self.envelope()
        options = dict(returned_model=envelope['model'],service_tier=envelope['service_tier'],expected_input_tokens=100)
        options.update(changes)
        return response_cost(envelope['usage'] if usage is None else usage,self.budget,**options)

    def test_new_wire_is_explicitly_versioned_and_bound(self):
        body = wire_body(self.req,**{key:value for key,value in self.options.items() if key!='base_url'})
        self.assertEqual(body['service_tier'],'default')
        self.assertEqual(body['prompt_cache_options'],dict(mode='implicit',ttl='30m'))
        self.assertEqual(body['max_completion_tokens'],128)
        self.assertNotIn('temperature',body)
        self.assertEqual(self.inv['format'],'okb-judge-cost-review-v4-draft')
        self.assertEqual(self.contract['format'],'okb-judge-runtime-contract-v4-draft')

    def test_legacy_wire_cache_mode_preserved(self):
        body = wire_body(self.req,'gpt-6.1-sol',128,wire_profile='reasoning-chat-v1',reasoning_effort='low')
        self.assertEqual(body['prompt_cache_options'],dict(mode='explicit',ttl='30m'))
        self.assertNotIn('service_tier',body)

    def test_unsupported_model_or_missing_effort_denied(self):
        for changes in (dict(model='other'),dict(reasoning_effort=None)):
            options = dict(self.options,**changes)
            options.pop('base_url')
            with self.assertRaises(ValueError): wire_body(self.req,**options)

    def test_scenario_reserves_most_expensive_input_bucket(self):
        estimate = scenario(self.inv,self.quote,self.tokens)
        self.assertEqual(estimate['total_cost'],'0.00153')
        self.assertEqual(estimate['largest_request_cost'],'0.00153')
        self.assertEqual(estimate['billing_profile'],PROFILE)
        self.assertFalse(self.contract['execution_authorized'])
        self.assertFalse(self.contract['hard_monetary_cap'])

    def test_any_input_bucket_can_be_most_expensive(self):
        quote = copy.deepcopy(self.quote)
        quote['rates']['input_cache_hit_per_million'] = '4'
        self.assertEqual(scenario(self.inv,quote,self.tokens)['total_cost'],'0.00168')

    def test_plain_input_reservation_cannot_underfund_writes(self):
        budget = dict(self.budget,reserve_per_request='0.00148')
        with self.assertRaises(ValueError): runtime_contract(self.inv,self.quote,self.tokens,budget,1)

    def test_wrong_quote_budget_rate_denied(self):
        budget = copy.deepcopy(self.budget)
        budget['rates']['input_cache_write_per_million'] = '0'
        with self.assertRaises(ValueError): runtime_contract(self.inv,self.quote,self.tokens,budget,1)

    def test_wrong_pricing_interval_denied(self):
        quote = copy.deepcopy(self.quote)
        quote['pricing_conditions']['min_input_tokens'] = 101
        with self.assertRaises(ValueError): scenario(self.inv,quote,self.tokens)

    def test_different_interval_must_be_requoted(self):
        quote = copy.deepcopy(self.quote)
        quote['pricing_conditions'].update(min_input_tokens=272001,max_input_tokens=1050000)
        tokens = copy.deepcopy(self.tokens)
        tokens['records'][0]['input_tokens'] = 272001
        self.assertEqual(scenario(self.inv,quote,tokens)['total_cost'],'0.6812825')

    def test_rates_require_all_four_fields_without_extra_fees(self):
        for field in tuple(self.rates)+('extra_fee',):
            quote = copy.deepcopy(self.quote)
            if field == 'extra_fee': quote['rates'][field] = '1'
            else: del quote['rates'][field]
            with self.subTest(field=field), self.assertRaises(ValueError): scenario(self.inv,quote,self.tokens)

    def test_invalid_amounts_and_currency_denied(self):
        for value in ('NaN','-1','1e1000','1e-19'):
            budget = copy.deepcopy(self.budget)
            budget['rates']['input_cache_write_per_million'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): validate_budget(budget)
        with self.assertRaises(ValueError): validate_budget(dict(self.budget,currency='usd'))

    def test_quote_model_endpoint_date_and_count_coverage_denied(self):
        for field,value in (('model','other'),('endpoint_sha256','0'*64),('observed_on','2026-9-30')):
            with self.subTest(field=field), self.assertRaises(ValueError): scenario(self.inv,dict(self.quote,**{field:value}),self.tokens)
        tokens = dict(self.tokens,records=[])
        with self.assertRaises(ValueError): scenario(self.inv,self.quote,tokens)

    def test_old_quote_cannot_price_new_wire(self):
        old = dict(format='okb-judge-price-quote-v1-draft',model='gpt-6.1-sol',endpoint_sha256=self.inv['endpoint_sha256'],
            currency='USD',input_per_million='2',output_per_million='10',price_source='SYNTHETIC TEST ONLY',observed_on='2026-09-30')
        with self.assertRaises(ValueError): scenario(self.inv,old,self.tokens)

    def test_wire_effort_and_current_counts_bound_to_contract(self):
        binding = validate_runtime_contract(self.contract,self.rows,budget=self.budget,max_calls=1,**self.options)
        self.assertEqual(binding['input_tokens'],{fingerprint(self.req):100})
        with self.assertRaises(ValueError):
            validate_runtime_contract(self.contract,self.rows,budget=self.budget,max_calls=1,**dict(self.options,reasoning_effort='high'))

    def test_budget_requires_contract_and_matching_wire_before_journal(self):
        for options in (dict(self.options),dict(self.options,wire_profile='reasoning-chat-v1')):
            with self.assertRaises(ValueError):
                AuditedFacts(journal_path=self.root/'denied.jsonl',budget=self.budget,max_calls=1,api_key='synthetic',**options)
        self.assertFalse((self.root/'denied.jsonl').exists())

    def test_four_buckets_disjoint_and_reasoning_not_double_charged(self):
        receipt = self.cost()
        self.assertEqual(receipt['estimated_cost'],'0.000477')
        self.assertEqual(receipt['token_buckets'],dict(input_uncached_tokens=50,input_cache_hit_tokens=20,input_cache_write_tokens=30,output_tokens=30))

    def test_explicit_zero_cache_counts_valid(self):
        usage = self.envelope()['usage']
        usage['prompt_tokens_details'].update(cached_tokens=0,cache_write_tokens=0)
        self.assertEqual(self.cost(usage)['estimated_cost'],'0.0005')

    def test_missing_cache_write_not_inferred_zero(self):
        usage = self.envelope()['usage']
        del usage['prompt_tokens_details']['cache_write_tokens']
        self.assertIsNone(self.cost(usage))

    def test_missing_cache_hit_and_usage_totals_unknown(self):
        for field in ('prompt_tokens','completion_tokens','total_tokens','prompt_tokens_details'):
            usage = self.envelope()['usage']
            del usage[field]
            with self.subTest(field=field): self.assertIsNone(self.cost(usage))
        usage = self.envelope()['usage']
        del usage['prompt_tokens_details']['cached_tokens']
        self.assertIsNone(self.cost(usage))

    def test_bool_negative_and_overlapping_counts_unknown(self):
        for value in (True,-1,101):
            usage = self.envelope()['usage']
            usage['prompt_tokens_details']['cache_write_tokens'] = value
            with self.subTest(value=value): self.assertIsNone(self.cost(usage))

    def test_total_and_reasoning_mismatch_unknown(self):
        usage = self.envelope()['usage']
        usage['total_tokens'] = 131
        self.assertIsNone(self.cost(usage))
        usage = self.envelope()['usage']
        usage['completion_tokens_details']['reasoning_tokens'] = 31
        self.assertIsNone(self.cost(usage))

    def test_unpriced_usage_details_and_extra_counters_unknown(self):
        usage = self.envelope()['usage']
        usage['prompt_tokens_details']['audio_tokens'] = 1
        self.assertIsNone(self.cost(usage))
        usage = self.envelope()['usage']
        usage['completion_tokens_details']['audio_tokens'] = 1
        self.assertIsNone(self.cost(usage))
        usage = self.envelope()['usage']
        usage['extra_billed_tokens'] = 1
        self.assertIsNone(self.cost(usage))

    def test_expected_full_count_tier_and_model_mismatch_unknown(self):
        for changes in (dict(expected_input_tokens=101),dict(service_tier='priority'),dict(service_tier=None),dict(returned_model='other')):
            with self.subTest(changes=changes): self.assertIsNone(self.cost(**changes))

    def test_generic_two_bucket_estimator_cannot_guess_cache_cost(self):
        self.assertIsNone(estimate_cost(self.envelope()['usage'],self.budget))
        budget = dict(currency='USD',input_per_million='2',output_per_million='10',price_source='SYNTHETIC TEST ONLY',
            max_total_cost='1',reserve_per_request='0.01')
        self.assertIsNone(estimate_cost(self.envelope()['usage'],budget))

    def test_native_text_and_prediction_counters_not_double_charged(self):
        usage = self.envelope()['usage']
        usage['prompt_tokens_details']['text_tokens'] = 90
        usage['completion_tokens_details'].update(text_tokens=20,accepted_prediction_tokens=5,rejected_prediction_tokens=3)
        self.assertEqual(self.cost(usage)['estimated_cost'],'0.000477')

    def test_native_diagnostic_counter_bounds_checked(self):
        for field,value in (('text_tokens',101),('text_tokens',True)):
            usage = self.envelope()['usage']
            usage['prompt_tokens_details'][field] = value
            with self.subTest(value=value): self.assertIsNone(self.cost(usage))
        usage = self.envelope()['usage']
        usage['completion_tokens_details']['rejected_prediction_tokens'] = 31
        self.assertIsNone(self.cost(usage))

    def test_input_and_requested_output_fit_published_context(self):
        quote = copy.deepcopy(self.quote)
        quote['pricing_conditions']['max_input_tokens'] = 2000000
        tokens = copy.deepcopy(self.tokens)
        tokens['records'][0]['input_tokens'] = 1050000
        with self.assertRaises(ValueError): scenario(self.inv,quote,tokens)

    def test_zero_audio_and_image_diagnostics_allowed(self):
        usage = self.envelope()['usage']
        usage['prompt_tokens_details'].update(audio_tokens=0,image_tokens=0)
        usage['completion_tokens_details']['audio_tokens'] = 0
        self.assertEqual(self.cost(usage)['estimated_cost'],'0.000477')

    def test_native_usage_diagnostics_reach_audit_and_group(self):
        group = self.group()
        client = self.client(scope='left',group=group)
        envelope = self.envelope()
        envelope['usage']['prompt_tokens_details']['text_tokens'] = 90
        envelope['usage']['completion_tokens_details']['text_tokens'] = 20
        with patch.object(client,'_exchange',return_value=envelope): client.judge(self.req)
        self.assertEqual(group.summary()['estimated_known_cost'],'0.000477')
        self.assertFalse(group.summary()['halted'])

    def test_duplicate_counts_and_bool_interval_denied(self):
        tokens = dict(self.tokens,records=self.tokens['records']*2)
        with self.assertRaises(ValueError): scenario(self.inv,self.quote,tokens)
        quote = copy.deepcopy(self.quote)
        quote['pricing_conditions']['min_input_tokens'] = True
        with self.assertRaises(ValueError): scenario(self.inv,quote,self.tokens)

    def test_full_batch_reservation_not_just_discounted_cost(self):
        second = request('second synthetic fact',{},self.req['observations'])
        rows = self.rows+[dict(request=second,request_sha256=fingerprint(second),source=dict(case_id='second'))]
        inv = inventory(rows,**self.options)
        tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'],records=[
            dict(wire_request_sha256=row['wire_request_sha256'],input_tokens=100) for row in inv['requests']])
        with self.assertRaises(ValueError): runtime_contract(inv,self.quote,tokens,dict(self.budget,max_total_cost='0.0031'),2)

    def test_wrong_tier_keeps_numeric_usage_and_halts_group(self):
        group = self.group()
        client = self.client(scope='left',group=group)
        envelope = self.envelope()
        envelope['service_tier'] = 'priority'
        with patch.object(client,'_exchange',return_value=envelope): client.judge(self.req)
        event = next(iter(read_journal(self.root/'calls.jsonl')['completed'].values()))
        self.assertEqual(event['usage']['total_tokens'],130)
        self.assertIsNone(event['estimated_cost'])
        self.assertTrue(group.summary()['halted'])

    def test_offline_review_binds_v4_and_preserves_exact_wire(self):
        from semantic_robustness import build_requests
        from review_judge_cost import main
        corpus = dict(format='okb-semantic-robustness-v1',case_count=1,cases=[dict(id='c',requirement='synthetic fact',
            context={},observations=self.req['observations'],expected='supported',category='fixture',equivalence_group=None)])
        rows = [dict(row,source=dict(kind='author_development_case',case_id=row['case_id'])) for row in build_requests(corpus)]
        inv = inventory(rows,**self.options)
        tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'])
        for name,value in dict(corpus=corpus,quote=self.quote,tokens=tokens,budget=self.budget).items():
            (self.root/(name+'.json')).write_text(canonical(value),encoding='utf8')
        out = self.root/'review'
        args = ['--corpus',str(self.root/'corpus.json'),'--model','gpt-6.1-sol','--base-url',self.options['base_url'],
            '--out-dir',str(out),'--max-output-tokens','128','--wire-profile',WIRE_PROFILE,'--reasoning-effort','low',
            '--quote',str(self.root/'quote.json'),'--token-counts',str(self.root/'tokens.json'),
            '--runtime-budget',str(self.root/'budget.json'),'--max-calls','1']
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(args),0)
        contract = json.loads((out/'runtime-contract.json').read_text(encoding='utf8'))
        self.assertEqual(contract['format'],'okb-judge-runtime-contract-v4-draft')
        self.assertFalse(contract['execution_authorized'])
        wire = json.loads((out/'wire-requests.json').read_text(encoding='utf8'))
        self.assertEqual(wire['records'][0]['body']['service_tier'],'default')
        self.assertEqual(fingerprint(wire['records'][0]['body']),inv['requests'][0]['wire_request_sha256'])

    def test_known_cache_cost_journal_and_group_binding(self):
        group = self.group()
        client = self.client(scope='left',group=group)
        with patch.object(client,'_exchange',return_value=self.envelope()): client.judge(self.req)
        self.assertEqual(group.summary()['estimated_known_cost'],'0.000477')
        journal = read_journal(self.root/'calls.jsonl')
        event = next(iter(journal['completed'].values()))
        self.assertEqual(event['cost_source'],'four_declared_disjoint_token_buckets')
        self.assertEqual(event['returned_service_tier'],'default')
        self.assertEqual(event['token_buckets']['input_cache_write_tokens'],30)

    def test_missing_write_usage_halts_entire_group(self):
        group = self.group()
        client = self.client(scope='left',group=group)
        envelope = self.envelope()
        del envelope['usage']['prompt_tokens_details']['cache_write_tokens']
        with patch.object(client,'_exchange',return_value=envelope): client.judge(self.req)
        self.assertEqual(group.summary()['unknown_calls'],1)
        with self.assertRaises(FactError): self.client(scope='right',group=group,name='right.jsonl')
        self.assertFalse((self.root/'right.jsonl').exists())

    def test_output_overrun_keeps_actual_cost_but_halts(self):
        group = self.group()
        client = self.client(scope='left',group=group)
        envelope = self.envelope()
        envelope['usage'].update(completion_tokens=129,total_tokens=229)
        with patch.object(client,'_exchange',return_value=envelope): client.judge(self.req)
        self.assertEqual(group.summary()['estimated_known_cost'],'0.001467')
        self.assertTrue(group.summary()['halted'])

    def test_invalid_json_still_accounts_known_cache_cost(self):
        group = self.group()
        client = self.client(scope='left',group=group)
        envelope = self.envelope()
        envelope['choices'][0]['message']['content'] = 'invalid'
        with patch.object(client,'_exchange',return_value=envelope), self.assertRaises(FactError): client.judge(self.req)
        self.assertEqual(group.summary()['estimated_known_cost'],'0.000477')

    def test_legacy_budget_never_prices_positive_cache_writes_as_plain_input(self):
        options = dict(self.options,wire_profile='reasoning-chat-v1')
        budget = dict(currency='USD',input_per_million='2',output_per_million='10',price_source='SYNTHETIC TEST ONLY',
            max_total_cost='1',reserve_per_request='0.01')
        client = AuditedFacts(journal_path=self.root/'legacy.jsonl',budget=budget,max_calls=1,api_key='synthetic',**options)
        with patch.object(client,'_exchange',return_value=self.envelope()): client.judge(self.req)
        self.assertEqual(client.unmeasured,1)
        self.assertTrue(client.halted)

    def test_development_cli_routes_v4_to_bound_transport(self):
        from semantic_robustness import build_requests, main
        from test_cost_group import group_cli_args
        corpus = dict(format='okb-semantic-robustness-v1',case_count=1,cases=[dict(id='c',requirement='synthetic fact',
            context={},observations=self.req['observations'],expected='supported',category='fixture',equivalence_group=None)])
        rows = [dict(row,source=dict(kind='author_development_case',case_id=row['case_id'])) for row in build_requests(corpus)]
        inv = inventory(rows,**self.options)
        tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'])
        contract = runtime_contract(inv,self.quote,tokens,self.budget,1)
        for name,value in dict(corpus=corpus,contract=contract,budget=self.budget).items():
            (self.root/(name+'.json')).write_text(canonical(value),encoding='utf8')
        out = self.root/'live'
        args = ['--corpus',str(self.root/'corpus.json'),'--out-dir',str(out),'--live','--authorize-paid-calls',
            '--model','gpt-6.1-sol','--base-url',self.options['base_url'],'--max-calls','1','--max-output-tokens','128',
            '--budget-file',str(self.root/'budget.json'),'--runtime-contract-file',str(self.root/'contract.json'),
            '--wire-profile',WIRE_PROFILE,'--reasoning-effort','low']+group_cli_args(self.root,contract)
        with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}), patch.object(AuditedFacts,'_exchange',return_value=self.envelope()) as transport, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args),0)
            transport.assert_called_once()
            self.assertEqual(transport.call_args.args[0]['service_tier'],'default')
        summary = json.loads((out/'summary.json').read_text(encoding='utf8'))
        self.assertEqual(summary['call_audit']['estimated_known_cost'],'0.000477')

    def test_scoped_score_cli_routes_v4_to_bound_transport(self):
        from test_report_trace import fixture
        from test_cost_group import group_cli_args
        from score_four_layer import main
        with testtmp.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            _,run,paper,plan = fixture(root)
            prepared = json.loads((root/'prepare/results.json').read_text(encoding='utf8'))
            rows = [dict(row,source=dict(kind='historical_prepare',task_id=task['task_id'],check_id=row['check_id']))
                for task in prepared for row in task['semantic_requests']]
            inv = inventory(rows,**self.options)
            tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'],records=[
                dict(wire_request_sha256=row['wire_request_sha256'],input_tokens=100) for row in inv['requests']])
            contract = runtime_contract(inv,self.quote,tokens,self.budget,len(rows))
            (root/'budget.json').write_text(canonical(self.budget),encoding='utf8')
            (root/'contract.json').write_text(canonical(contract),encoding='utf8')
            def exchange(body):
                envelope = self.envelope()
                envelope['choices'][0]['message']['content'] = canonical(dict(result='insufficient',rationale='synthetic fixture',citations=[]))
                return envelope
            out = root/'live'
            args = ['--paper',str(paper),'--plan',str(plan),'--run-dir',str(run),'--out-dir',str(out),
                '--outcome-version','v2','--evidence-policy','scoped-v1','--live','--authorize-paid-calls',
                '--model','gpt-6.1-sol','--base-url',self.options['base_url'],'--max-calls',str(len(rows)),
                '--max-output-tokens','128','--budget-file',str(root/'budget.json'),
                '--runtime-contract-file',str(root/'contract.json'),'--wire-profile',WIRE_PROFILE,'--reasoning-effort','low']+group_cli_args(root,contract)
            with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}),patch.object(AuditedFacts,'_exchange',side_effect=exchange) as transport:
                self.assertEqual(main(args),0)
                self.assertEqual(transport.call_count,len(rows))
            self.assertEqual(read_journal(out/'call-journal.jsonl')['batch']['runtime_contract_sha256'],contract['contract_sha256'])


if __name__ == '__main__':
    unittest.main()
