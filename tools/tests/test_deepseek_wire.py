"""DeepSeek-native wire and accounting checks; synthetic transports and prices only."""
import contextlib
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
from scoring.audited_facts import AuditedFacts, deepseek_usage_valid, read_journal
from scoring.cost_review import inventory, scenario, wire_body
from scoring.cost_runtime import runtime_contract, validate_runtime_contract
from scoring.fact_judge import FactError, canonical, fingerprint, request
from review_judge_cost import main as review


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.req = request('fact', {}, [dict(evidence_id='e', content={'text':'fact'})])
        self.rows = [dict(request=self.req,request_sha256=fingerprint(self.req),source={'case_id':'fixture'})]
        self.options = dict(model='deepseek-flash',base_url='https://example.invalid/v1',
            max_output_tokens=1024,wire_profile='deepseek-thinking-v1',reasoning_effort='low')
        self.inv = inventory(self.rows, **self.options)
        self.quote = dict(format='okb-judge-price-quote-v1-draft',model='deepseek-flash',
            endpoint_sha256=self.inv['endpoint_sha256'],currency='USD',input_per_million='2',
            output_per_million='4',price_source='SYNTHETIC TEST ONLY',observed_on='2026-09-30')
        self.tokens = dict(format='okb-judge-token-counts-v1-draft',model='deepseek-flash',
            endpoint_sha256=self.inv['endpoint_sha256'],protocol=self.inv['protocol'],
            request_set_sha256=self.inv['request_set_sha256'],method='synthetic',source='fixture',
            records=[dict(wire_request_sha256=self.inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        self.budget = dict(currency='USD',input_per_million='2',output_per_million='4',
            price_source='SYNTHETIC TEST ONLY',max_total_cost='1',reserve_per_request='0.01')

    def contract(self):
        return runtime_contract(self.inv,self.quote,self.tokens,self.budget,1)

    def client(self, name='calls.jsonl'):
        return AuditedFacts(journal_path=self.root/name,budget=self.budget,max_calls=1,
            api_key='synthetic',runtime_contract=self.contract(),reviewed_requests=self.rows,**self.options)

    def envelope(self, **changes):
        answer = dict(result='supported',rationale='fixture',
            citations=[dict(evidence_id='e',pointer='/text',quote='fact')])
        usage = dict(prompt_tokens=100,completion_tokens=1024,total_tokens=1124,
            prompt_cache_hit_tokens=80,prompt_cache_miss_tokens=20,
            prompt_tokens_details=dict(cached_tokens=80),completion_tokens_details=dict(reasoning_tokens=900))
        return dict(model='deepseek-flash',usage=usage,
            choices=[dict(finish_reason='stop',message=dict(content=canonical(answer)))],**changes)

    def test_native_thinking_limit_is_not_gpt_completion_parameter(self):
        body = wire_body(self.req,'deepseek-flash',1024,
            wire_profile='deepseek-thinking-v1',reasoning_effort='low')
        self.assertEqual(set(body),{'model','messages','thinking','reasoning_effort','max_tokens'})
        self.assertEqual(body['thinking'],dict(type='enabled')); self.assertEqual(body['max_tokens'],1024)
        self.assertEqual(body['reasoning_effort'],'low')
        for limit in (1,32768):
            self.assertEqual(wire_body(self.req,'deepseek-flash',limit,
                wire_profile='deepseek-thinking-v1',reasoning_effort='low')['max_tokens'],limit)
        for limit in (0,True,32769):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                wire_body(self.req,'deepseek-flash',limit,wire_profile='deepseek-thinking-v1',reasoning_effort='low')

    def test_only_explicit_native_efforts_and_exact_model_are_accepted(self):
        for effort in ('low','high','max'):
            self.assertEqual(wire_body(self.req,'deepseek-flash',1024,
                wire_profile='deepseek-thinking-v1',reasoning_effort=effort)['reasoning_effort'],effort)
        for effort in (None,'none','medium','xhigh','minimal',True):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                wire_body(self.req,'deepseek-flash',1024,wire_profile='deepseek-thinking-v1',reasoning_effort=effort)
        for model in ('gpt-6.1-sol','deepseekflash4.1','deepseek-v4-flash','unselected'):
            with self.subTest(model=model), self.assertRaises(ValueError):
                wire_body(self.req,model,1024,wire_profile='deepseek-thinking-v1',reasoning_effort='low')

    def test_deepseek_cannot_accidentally_use_other_profiles(self):
        for model in ('deepseek-flash','deepseek-v4-flash','deepseekflash4.1','DeepSeek-V4.1-Flash'):
            for profile,effort in (('legacy-chat-v1',None),('reasoning-chat-v1','low')):
                with self.subTest(model=model,profile=profile), self.assertRaises(ValueError):
                    wire_body(self.req,model,1024,wire_profile=profile,reasoning_effort=effort)

    def test_v3_contract_binds_native_body_and_preserves_old_versions(self):
        contract = self.contract()
        self.assertEqual(self.inv['format'],'okb-judge-cost-review-v3-draft')
        self.assertEqual(contract['format'],'okb-judge-runtime-contract-v3-draft')
        binding = validate_runtime_contract(contract,self.rows,budget=self.budget,max_calls=1,**self.options)
        self.assertEqual(binding['wire_requests'][fingerprint(self.req)],self.inv['requests'][0]['wire_request_sha256'])
        for change in (dict(reasoning_effort='high'),dict(max_output_tokens=2048),dict(base_url='https://another.invalid')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_runtime_contract(contract,self.rows,budget=self.budget,max_calls=1,**dict(self.options,**change))
        for model,profile,effort,version in (
                ('fixture','legacy-chat-v1',None,'v1'),('gpt-6.1-sol','reasoning-chat-v1','low','v2')):
            inv = inventory(self.rows,**dict(self.options,model=model,wire_profile=profile,reasoning_effort=effort))
            self.assertEqual(inv['format'],'okb-judge-cost-review-'+version+'-draft')

    def test_scenario_includes_total_generation_once(self):
        estimate = scenario(self.inv,self.quote,self.tokens)
        self.assertEqual(self.inv['output_token_tolerance'],0)
        self.assertEqual(estimate['output_tokens_if_every_call_hits_limit'],1024)
        self.assertEqual(estimate['total_cost'],'0.004296')
        self.assertFalse(self.contract()['execution_authorized'])

    def test_native_usage_requires_consistent_numeric_totals_and_cache_buckets(self):
        usage = self.envelope()['usage']; self.assertTrue(deepseek_usage_valid(usage))
        for field in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens'):
            missing = dict(usage); missing.pop(field)
            with self.subTest(missing=field): self.assertFalse(deepseek_usage_valid(missing))
        for changes in (dict(total_tokens=100),dict(prompt_cache_miss_tokens=0),dict(completion_tokens=True),
                dict(completion_tokens_details=dict(reasoning_tokens=1025)),
                dict(prompt_tokens_details=dict(cached_tokens=79)),dict(prompt_tokens_details=None)):
            with self.subTest(changes=changes): self.assertFalse(deepseek_usage_valid(dict(usage,**changes)))

    def test_reasoning_is_not_double_charged_and_cache_discount_is_not_assumed(self):
        client = self.client()
        with patch.object(client,'_exchange',return_value=self.envelope()) as http:
            self.assertEqual(client.judge(self.req)['result'],'supported'); http.assert_called_once()
        journal = read_journal(self.root/'calls.jsonl')
        self.assertEqual(journal['batch']['wire_profile'],'deepseek-thinking-v1')
        terminal = next(iter(journal['terminal'].values()))
        self.assertEqual(terminal['estimated_cost'],'0.004296')
        self.assertEqual(terminal['usage']['completion_tokens_details']['reasoning_tokens'],900)

    def test_invalid_usage_keeps_numeric_evidence_and_halts_without_resend(self):
        client = self.client(); envelope = self.envelope(); envelope['usage']['total_tokens'] = 100
        with patch.object(client,'_exchange',return_value=envelope) as http:
            client.judge(self.req)
            with self.assertRaises(FactError): client.judge(self.req)
            http.assert_called_once()
        terminal = next(iter(read_journal(self.root/'calls.jsonl')['terminal'].values()))
        self.assertIsNone(terminal['estimated_cost']); self.assertEqual(terminal['usage'],envelope['usage'])
        self.assertTrue(client.halted); self.assertEqual(client.unmeasured,1)

    def test_returned_model_mismatch_is_unknown_cost_not_an_alias_success(self):
        client = self.client(); envelope = self.envelope(); envelope['model'] = 'DeepSeek-V4.1-Flash'
        with patch.object(client,'_exchange',return_value=envelope):
            with self.assertRaises(FactError): client.judge(self.req)
        terminal = next(iter(read_journal(self.root/'calls.jsonl')['terminal'].values()))
        self.assertEqual(terminal['returned_model'],'DeepSeek-V4.1-Flash')
        self.assertIsNone(terminal['estimated_cost']); self.assertEqual(terminal['usage'],envelope['usage'])
        self.assertTrue(client.halted)

    def test_native_generation_breach_halts_under_reservation(self):
        client = self.client(); envelope = self.envelope()
        envelope['usage'].update(completion_tokens=1025,total_tokens=1125)
        with patch.object(client,'_exchange',return_value=envelope): client.judge(self.req)
        self.assertTrue(client.halted); self.assertEqual(client.unmeasured,0)
        self.assertLess(float(client.audit_summary()['estimated_known_cost']),0.01)

    def test_unknown_transport_is_never_automatically_resent(self):
        client = self.client()
        with patch.object(client,'_exchange',side_effect=RuntimeError('synthetic interruption')) as http:
            for attempt in (1,2):
                with self.subTest(attempt=attempt), self.assertRaises(FactError): client.judge(self.req)
            http.assert_called_once()
        self.assertEqual(read_journal(self.root/'calls.jsonl')['unknown_requests'],[fingerprint(self.req)])

    def test_review_cli_prepares_native_profile_without_model_http(self):
        out = self.root/'prepared'
        args = ['--out-dir',str(out),'--model','deepseek-flash','--base-url','https://example.invalid/v1',
            '--max-output-tokens','1024','--wire-profile','deepseek-thinking-v1','--reasoning-effort','low']
        with patch('urllib.request.build_opener') as http, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review(args),0); http.assert_not_called()
        report = json.loads((out/'review.json').read_text(encoding='utf8'))
        self.assertEqual(report['calls_if_all_dispatched_once'],33)
        self.assertEqual(report['format'],'okb-judge-cost-review-v3-draft')
        self.assertIsNone(report['estimate']['total_cost']); self.assertFalse(report['execution_authorized'])

    def test_development_cli_forwards_native_body_and_rejects_mismatched_contract_before_output(self):
        from semantic_robustness import build_requests, main
        corpus = dict(format='okb-semantic-robustness-v1',case_count=1,cases=[dict(id='c',requirement='fact',
            context={},observations=self.req['observations'],expected='supported',category='fixture',equivalence_group=None)])
        rows = [dict(row,source=dict(kind='author_development_case',case_id=row['case_id'])) for row in build_requests(corpus)]
        inv = inventory(rows,**self.options)
        tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'],
            records=[dict(wire_request_sha256=inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        contract = runtime_contract(inv,self.quote,tokens,self.budget,1)
        for name,value in dict(corpus=corpus,contract=contract,budget=self.budget).items():
            (self.root/(name+'.json')).write_text(canonical(value),encoding='utf8')
        out = self.root/'live'
        args = ['--corpus',str(self.root/'corpus.json'),'--runtime-contract-file',str(self.root/'contract.json'),
            '--budget-file',str(self.root/'budget.json'),'--out-dir',str(out),'--live','--authorize-paid-calls',
            '--model','deepseek-flash','--base-url','https://example.invalid/v1','--max-calls','1','--max-output-tokens','1024',
            '--wire-profile','deepseek-thinking-v1','--reasoning-effort','low']
        with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}), patch.object(AuditedFacts,'_exchange',
                side_effect=RuntimeError('synthetic interruption')) as http, contextlib.redirect_stdout(io.StringIO()):
            from test_cost_group import group_cli_args
            args += group_cli_args(self.root, contract)
            self.assertEqual(main(args),1); http.assert_called_once()
            self.assertIn('max_tokens',http.call_args.args[0]); self.assertNotIn('max_completion_tokens',http.call_args.args[0])
            mismatch = self.root/'mismatch'
            args[args.index('--out-dir')+1] = str(mismatch); args[args.index('--reasoning-effort')+1] = 'high'
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(main(args),2)
            http.assert_called_once(); self.assertFalse(mismatch.exists())

    def test_scoped_score_cli_forwards_native_body(self):
        sys.path.insert(0,str(Path(__file__).parent))
        from test_report_trace import fixture
        from score_four_layer import main as score
        with testtmp.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory); _,run,paper,plan = fixture(root)
            prepared = json.loads((root/'prepare/results.json').read_text(encoding='utf8'))
            rows = [dict(row,source=dict(kind='historical_prepare',task_id=task['task_id'],check_id=row['check_id']))
                    for task in prepared for row in task['semantic_requests']]
            inv = inventory(rows,**self.options)
            tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'],
                records=[dict(wire_request_sha256=row['wire_request_sha256'],input_tokens=100) for row in inv['requests']])
            contract = runtime_contract(inv,self.quote,tokens,self.budget,len(rows))
            (root/'contract.json').write_text(canonical(contract),encoding='utf8')
            (root/'budget.json').write_text(canonical(self.budget),encoding='utf8')
            out = root/'live'
            args = ['--paper',str(paper),'--plan',str(plan),'--run-dir',str(run),'--out-dir',str(out),
                '--outcome-version','v2','--evidence-policy','scoped-v1','--live','--authorize-paid-calls',
                '--model','deepseek-flash','--base-url','https://example.invalid/v1','--max-calls',str(len(rows)),
                '--max-output-tokens','1024','--budget-file',str(root/'budget.json'),
                '--runtime-contract-file',str(root/'contract.json'),'--wire-profile','deepseek-thinking-v1','--reasoning-effort','low']
            with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}),patch.object(AuditedFacts,'_exchange',
                    side_effect=RuntimeError('synthetic interruption')) as http:
                from test_cost_group import group_cli_args
                args += group_cli_args(root, contract)
                self.assertEqual(score(args),0); http.assert_called_once()
                self.assertIn('max_tokens',http.call_args.args[0]); self.assertNotIn('max_completion_tokens',http.call_args.args[0])
            self.assertEqual(read_journal(out/'call-journal.jsonl')['batch']['wire_profile'],'deepseek-thinking-v1')


if __name__ == '__main__':
    unittest.main()
