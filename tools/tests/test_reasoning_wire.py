"""Synthetic pricing and transports only; user credentials are never used."""
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
from scoring.audited_facts import AuditedFacts, read_journal
from scoring.cost_review import inventory, scenario, wire_body
from scoring.cost_runtime import runtime_contract, validate_runtime_contract
from scoring.fact_judge import FactError, LiveFacts, canonical, fingerprint, request
from review_judge_cost import main as review


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.req = request('fact', {}, [dict(evidence_id='e', content={'text':'fact'})])
        self.rows = [dict(request=self.req,request_sha256=fingerprint(self.req),source={'case_id':'fixture'})]
        self.options = dict(model='gpt-6.1-sol',base_url='https://example.invalid/v1',
            max_output_tokens=128,wire_profile='reasoning-chat-v1',reasoning_effort='low')
        self.inv = inventory(self.rows, **self.options)
        self.quote = dict(format='okb-judge-price-quote-v1-draft',model='gpt-6.1-sol',
            endpoint_sha256=self.inv['endpoint_sha256'],currency='USD',input_per_million='2',
            output_per_million='4',price_source='SYNTHETIC TEST ONLY',observed_on='2026-09-30')
        self.tokens = dict(format='okb-judge-token-counts-v1-draft',model='gpt-6.1-sol',
            endpoint_sha256=self.inv['endpoint_sha256'],protocol=self.inv['protocol'],
            request_set_sha256=self.inv['request_set_sha256'],method='synthetic',source='fixture',
            records=[dict(wire_request_sha256=self.inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        self.budget = dict(currency='USD',input_per_million='2',output_per_million='4',
            price_source='SYNTHETIC TEST ONLY',max_total_cost='1',reserve_per_request='0.01')

    def contract(self):
        return runtime_contract(self.inv,self.quote,self.tokens,self.budget,1)

    def test_wire_omits_unsupported_sampling_and_answer_only_limit(self):
        body = wire_body(self.req,'gpt-6.1-sol',128,wire_profile='reasoning-chat-v1',reasoning_effort='low')
        self.assertNotIn('temperature',body); self.assertNotIn('max_tokens',body)
        self.assertEqual(body['max_completion_tokens'],128); self.assertEqual(body['reasoning_effort'],'low')
        self.assertEqual(body['prompt_cache_options'],dict(mode='explicit',ttl='30m'))

    def test_known_reasoning_models_reject_legacy_profile(self):
        for model in ('gpt-6.1-sol','qwen3.8-max'):
            with self.subTest(model=model), self.assertRaises(ValueError): wire_body(self.req,model,128)

    def test_reasoning_effort_is_explicit_and_profile_bound(self):
        for effort in (None,'none','minimal',True):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                wire_body(self.req,'gpt-6.1-sol',128,wire_profile='reasoning-chat-v1',reasoning_effort=effort)
        with self.assertRaises(ValueError):
            wire_body(self.req,'fixture',128,reasoning_effort='low')
        qwen = wire_body(self.req,'qwen3.8-max',128,wire_profile='reasoning-chat-v1',reasoning_effort='max')
        self.assertEqual(qwen['reasoning_effort'],'max'); self.assertNotIn('prompt_cache_options',qwen)

    def test_v2_contract_reconstructs_and_cannot_be_reused_with_other_effort(self):
        contract = self.contract()
        self.assertEqual(contract['format'],'okb-judge-runtime-contract-v2-draft')
        self.assertEqual(self.inv['format'],'okb-judge-cost-review-v2-draft')
        binding = validate_runtime_contract(contract,self.rows,budget=self.budget,max_calls=1,**self.options)
        self.assertEqual(binding['contract_sha256'],contract['contract_sha256'])
        with self.assertRaises(ValueError):
            validate_runtime_contract(contract,self.rows,budget=self.budget,max_calls=1,
                **dict(self.options,reasoning_effort='medium'))

    def test_qwen_documented_output_tolerance_is_included_in_scenario(self):
        options = dict(self.options,model='qwen3.8-max')
        inv = inventory(self.rows,**options)
        quote = dict(self.quote,model='qwen3.8-max')
        tokens = dict(self.tokens,model='qwen3.8-max',request_set_sha256=inv['request_set_sha256'],
            records=[dict(wire_request_sha256=inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        estimate = scenario(inv,quote,tokens)
        self.assertEqual(inv['output_token_tolerance'],10)
        self.assertEqual(estimate['output_tokens_if_every_call_hits_limit'],138)
        self.assertEqual(estimate['total_cost'],'0.000752')

    def test_token_plan_endpoint_rejected_without_journal_or_http(self):
        endpoint = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
        journal = self.root/'denied.jsonl'
        with patch('urllib.request.build_opener') as http:
            with self.assertRaises(FactError):
                AuditedFacts(journal_path=journal,budget=self.budget,max_output_tokens=128,
                    base_url=endpoint,api_key='synthetic',model='qwen3.8-max',max_calls=1,
                    wire_profile='reasoning-chat-v1',reasoning_effort='low')
            http.assert_not_called()
        self.assertFalse(journal.exists())

    def test_reasoning_body_and_contract_metadata_reach_durable_journal(self):
        client = AuditedFacts(journal_path=self.root/'calls.jsonl',budget=self.budget,
            max_calls=1,api_key='synthetic',runtime_contract=self.contract(),reviewed_requests=self.rows,**self.options)
        with patch.object(client,'_exchange',side_effect=RuntimeError('synthetic unknown consumption')) as http:
            with self.assertRaises(FactError): client.judge(self.req)
            http.assert_called_once_with(wire_body(self.req,'gpt-6.1-sol',128,
                wire_profile='reasoning-chat-v1',reasoning_effort='low'))
        journal = read_journal(self.root/'calls.jsonl')
        self.assertEqual(journal['batch']['wire_profile'],'reasoning-chat-v1')
        self.assertEqual(journal['batch']['reasoning_effort'],'low')
        self.assertTrue(client.halted)

    def test_completion_budget_breach_halts_even_when_cost_is_within_reservation(self):
        client = AuditedFacts(journal_path=self.root/'over-limit.jsonl',budget=self.budget,
            max_calls=1,api_key='synthetic',runtime_contract=self.contract(),reviewed_requests=self.rows,**self.options)
        answer = dict(result='supported',rationale='fixture',citations=[dict(evidence_id='e',pointer='/text',quote='fact')])
        envelope = dict(model='gpt-6.1-sol',usage=dict(prompt_tokens=100,completion_tokens=129),
            choices=[dict(finish_reason='stop',message=dict(content=canonical(answer)))])
        with patch.object(client,'_exchange',return_value=envelope): client.judge(self.req)
        self.assertTrue(client.halted)
        terminal = next(iter(read_journal(self.root/'over-limit.jsonl')['terminal'].values()))
        self.assertIsNotNone(terminal['estimated_cost']); self.assertTrue(terminal['batch_halted'])

    def test_review_cli_prepares_user_model_without_prices_or_credentials(self):
        out = self.root/'prepared'
        args = ['--out-dir',str(out),'--model','gpt-6.1-sol','--base-url','https://example.invalid/v1',
            '--max-output-tokens','1024','--wire-profile','reasoning-chat-v1','--reasoning-effort','low']
        with patch('urllib.request.build_opener') as http, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review(args),0); http.assert_not_called()
        report = json.loads((out/'review.json').read_text(encoding='utf8'))
        self.assertEqual(report['calls_if_all_dispatched_once'],33)
        self.assertIsNone(report['estimate']['total_cost']); self.assertFalse(report['execution_authorized'])

    def test_development_cli_legacy_gpt_pricing_denied_without_transport(self):
        from semantic_robustness import build_requests, main
        corpus = dict(format='okb-semantic-robustness-v1',case_count=1,cases=[dict(id='c',requirement='fact',
            context={},observations=self.req['observations'],expected='supported',category='fixture',equivalence_group=None)])
        rows = [dict(row,source=dict(kind='author_development_case',case_id=row['case_id'])) for row in build_requests(corpus)]
        inv = inventory(rows,**self.options)
        tokens = dict(self.tokens,request_set_sha256=inv['request_set_sha256'],
            records=[dict(wire_request_sha256=inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        contract = runtime_contract(inv,self.quote,tokens,self.budget,1)
        files = {'corpus':corpus,'contract':contract,'budget':self.budget}
        for name,value in files.items(): (self.root/(name+'.json')).write_text(canonical(value),encoding='utf8')
        out = self.root/'live'
        args = ['--corpus',str(self.root/'corpus.json'),'--runtime-contract-file',str(self.root/'contract.json'),
            '--budget-file',str(self.root/'budget.json'),'--out-dir',str(out),'--live','--authorize-paid-calls',
            '--model','gpt-6.1-sol','--base-url','https://example.invalid/v1','--max-calls','1','--max-output-tokens','128',
            '--wire-profile','reasoning-chat-v1','--reasoning-effort','low']
        with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}), patch.object(AuditedFacts,'_exchange',
                side_effect=RuntimeError('synthetic interruption')) as http, contextlib.redirect_stdout(io.StringIO()):
            from test_cost_group import group_cli_args
            args += group_cli_args(self.root, contract)
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(main(args),2)
            http.assert_not_called()
        self.assertFalse(out.exists())

    def test_scoped_score_cli_legacy_gpt_pricing_denied_without_transport(self):
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
                '--model','gpt-6.1-sol','--base-url','https://example.invalid/v1','--max-calls',str(len(rows)),
                '--max-output-tokens','128','--budget-file',str(root/'budget.json'),
                '--runtime-contract-file',str(root/'contract.json'),'--wire-profile','reasoning-chat-v1','--reasoning-effort','low']
            with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}),patch.object(AuditedFacts,'_exchange',
                    side_effect=RuntimeError('synthetic interruption')) as http:
                from test_cost_group import group_cli_args
                args += group_cli_args(root, contract)
                with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(score(args),2)
                http.assert_not_called()
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
