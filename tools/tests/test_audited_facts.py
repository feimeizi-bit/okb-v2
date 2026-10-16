"""Offline only: billing metadata, journal crash recovery and no resend."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
import io
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools')]
from scoring.fact_judge import FactError, request, canonical
from scoring.audited_facts import AuditedFacts, read_journal, recovered_records, estimate_cost, validate_budget


def budget():
    return dict(currency='USD',max_total_cost='1',reserve_per_request='0.1',
                input_per_million='2',output_per_million='4',price_source='synthetic test prices')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'calls.jsonl'
        self.req=request('fact',{},[dict(evidence_id='e',content={'text':'fact'})])
        self.answer=dict(result='supported',rationale='fixture',citations=[dict(evidence_id='e',pointer='/text',quote='fact')])
        self.envelope=dict(model='test-model',usage={'prompt_tokens':100,'completion_tokens':20,
             'completion_tokens_details':{'reasoning_tokens':10}},
             choices=[dict(finish_reason='stop',message={'content':canonical(self.answer)})])
    def client(self,**kwargs):
        options=dict(journal_path=self.path,budget=budget(),max_output_tokens=128,
                     base_url='https://example.invalid/v1',api_key='synthetic-key-never-log',model='test-model',max_calls=3)
        options.update(kwargs);return AuditedFacts(**options)
    def test_pending_before_dispatch_durable_response_and_usage(self):
        j=self.client()
        def exchange(body):
            self.assertEqual(body['max_tokens'],128)
            self.assertEqual(len(read_journal(self.path)['unknown_requests']),1)
            return self.envelope
        with patch.object(j,'_exchange',side_effect=exchange):self.assertEqual(j.judge(self.req),self.answer)
        saved=read_journal(self.path)
        event=next(iter(saved['completed'].values()))
        self.assertEqual(event['usage'],self.envelope['usage'])
        self.assertEqual(event['estimated_cost'],'0.00028')
        self.assertNotIn('synthetic-key',self.path.read_text())
        self.assertEqual(len(recovered_records(self.path,model='test-model')),1)
        self.assertEqual(saved['unknown_requests'],[])
    def test_timeout_halts_no_retry_or_resend(self):
        j=self.client()
        with patch.object(j,'_exchange',side_effect=TimeoutError('private-body')) as http:
            with self.assertRaises(FactError):j.judge(self.req)
            with self.assertRaises(FactError):j.judge(self.req)
            changed=dict(self.req,requirement='other')
            with self.assertRaises(FactError):j.judge(changed)
            self.assertEqual(http.call_count,1)
        self.assertEqual(len(read_journal(self.path)['unknown_requests']),1)
        self.assertNotIn('private-body',self.path.read_text());self.assertTrue(j.halted)
    def test_interruption_leaves_pending_no_auto_resume(self):
        j=self.client()
        with patch.object(j,'_exchange',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):j.judge(self.req)
        self.assertEqual(len(read_journal(self.path)['unknown_requests']),1)
        with self.assertRaises(FileExistsError):self.client()
        self.assertEqual(recovered_records(self.path,model='test-model'),[])
    def test_budget_and_call_cap_before_send(self):
        b=budget();b['max_total_cost']='0.1'
        j=self.client(budget=b)
        with patch.object(j,'_exchange',return_value=self.envelope) as http:
            j.judge(self.req)
            with self.assertRaises(FactError):j.judge(dict(self.req,requirement='other'))
            self.assertEqual(http.call_count,1)
        self.assertEqual(len(read_journal(self.path)['pending']),1)
    def test_failed_structure_keeps_usage(self):
        j=self.client();self.envelope['choices'][0]['message']['content']='not json'
        with patch.object(j,'_exchange',return_value=self.envelope):
            with self.assertRaises(FactError):j.judge(self.req)
        terminal=next(iter(read_journal(self.path)['terminal'].values()))
        self.assertEqual(terminal['kind'],'invalid_response');self.assertIsNotNone(terminal['estimated_cost'])
        self.assertEqual(j.records,[])
    def test_missing_usage_halts_does_not_fabricate_zero_cost(self):
        j=self.client();del self.envelope['usage']
        with patch.object(j,'_exchange',return_value=self.envelope):j.judge(self.req)
        self.assertTrue(j.halted);self.assertEqual(j.audit_summary()['unmeasured_calls'],1)
        self.assertIsNone(next(iter(read_journal(self.path)['completed'].values()))['estimated_cost'])
    def test_model_mismatch_and_truncation_are_rejected(self):
        for change in ['model','finish']:
            path=Path(self.temp.name)/(change+'.jsonl');j=self.client(journal_path=path)
            e=copy.deepcopy(self.envelope)
            if change=='model':e['model']='different'
            else:e['choices'][0]['finish_reason']='length'
            with patch.object(j,'_exchange',return_value=e):
                with self.assertRaises(FactError):j.judge(self.req)
            self.assertEqual(recovered_records(path,model='test-model'),[])
    def test_provider_exceeds_reserve_stops_further_spend(self):
        j=self.client();self.envelope['usage']['prompt_tokens']=1_000_000
        with patch.object(j,'_exchange',return_value=self.envelope):j.judge(self.req)
        self.assertTrue(j.halted);self.assertFalse(j.audit_summary()['hard_monetary_cap'])
    def test_torn_tail_and_hash_tamper(self):
        j=self.client()
        with patch.object(j,'_exchange',return_value=self.envelope):j.judge(self.req)
        with self.path.open('ab') as f:f.write(b'{"partial":')
        self.assertTrue(read_journal(self.path)['incomplete_tail'])
        self.assertEqual(len(recovered_records(self.path,model='test-model')),1)
        text=self.path.read_text();self.path.write_text(text.replace('fixture','changed'),encoding='utf8')
        with self.assertRaises(FactError):read_journal(self.path)
    def test_failed_pending_write_never_calls_network(self):
        j=self.client()
        with patch.object(j,'_append',side_effect=FactError('disk')),patch.object(j,'_exchange') as http:
            with self.assertRaises(FactError):j.judge(self.req)
            http.assert_not_called()
    def test_no_double_count_reasoning_or_invented_usage(self):
        self.assertEqual(estimate_cost(self.envelope['usage'],budget()),'0.00028')
        self.assertIsNone(estimate_cost({'prompt_tokens':True,'completion_tokens':2},budget()))
    def test_invalid_budget_rejected(self):
        for field,value in [('reserve_per_request','0'),('max_total_cost','NaN'),('input_per_million',-1)]:
            b=budget();b[field]=value
            with self.assertRaises(FactError):validate_budget(b)
    def test_output_limit_required(self):
        for n in [None,True,0,32769]:
            with self.assertRaises(FactError):self.client(max_output_tokens=n)
        self.assertFalse(self.path.exists())
    def test_recovery_cannot_change_model(self):
        self.client()
        with self.assertRaises(FactError):recovered_records(self.path,model='other')
    def test_terminal_disk_failure_retains_pending(self):
        j=self.client();original=j._append
        def append(event):
            if event['kind']=='completed':raise OSError('disk full')
            return original(event)
        with patch.object(j,'_exchange',return_value=self.envelope),patch.object(j,'_append',side_effect=append):
            with self.assertRaises(OSError):j.judge(self.req)
        self.assertEqual(j.records,[])
        self.assertEqual(len(read_journal(self.path)['unknown_requests']),1)
    def test_corrupt_complete_line_rejected(self):
        self.client()
        with self.path.open('ab') as f:f.write(b'not-json\n')
        with self.assertRaises(FactError):read_journal(self.path)
    def test_protocol_mismatch_before_dispatch(self):
        j=self.client()
        with patch.object(j,'_exchange') as http:
            with self.assertRaises(FactError):j.judge(dict(self.req,protocol='other'))
            http.assert_not_called()
        self.assertEqual(len(read_journal(self.path)['pending']),0)
    def test_robustness_cli_audits_and_offline_recovers(self):
        from semantic_robustness import main
        from recover_fact_journal import main as recover
        corpus=dict(format='okb-semantic-robustness-v1',case_count=1,cases=[dict(id='c',requirement='fact',
            context={},observations=self.req['observations'],expected='supported',category='fixture',equivalence_group=None)])
        src=Path(self.temp.name)/'cases.json';src.write_text(json.dumps(corpus),encoding='utf8')
        bp=Path(self.temp.name)/'budget.json';bp.write_text(json.dumps(budget()),encoding='utf8')
        out=Path(self.temp.name)/'live';rp=Path(self.temp.name)/'replay.jsonl'
        args=['--corpus',str(src),'--out-dir',str(out),'--live','--authorize-paid-calls','--model','test-model',
              '--base-url','https://example.invalid/v1','--max-calls','1','--max-output-tokens','128','--budget-file',str(bp)]
        from review_judge_cost import main as review
        from scoring.cost_review import inventory
        from semantic_robustness import build_requests
        rows=[dict(row,source=dict(kind='author_development_case',case_id=row['case_id'])) for row in build_requests(corpus)]
        inv=inventory(rows,model='test-model',base_url='https://example.invalid/v1',max_output_tokens=128)
        quote=dict(format='okb-judge-price-quote-v1-draft',model='test-model',endpoint_sha256=inv['endpoint_sha256'],
            currency='USD',input_per_million='2',output_per_million='4',price_source=budget()['price_source'],observed_on='2026-09-30')
        tokens=dict(format='okb-judge-token-counts-v1-draft',model='test-model',endpoint_sha256=inv['endpoint_sha256'],
            protocol=inv['protocol'],request_set_sha256=inv['request_set_sha256'],method='synthetic',source='fixture',
            records=[dict(wire_request_sha256=inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        qp=Path(self.temp.name)/'quote.json';qp.write_text(json.dumps(quote),encoding='utf8')
        tp=Path(self.temp.name)/'tokens.json';tp.write_text(json.dumps(tokens),encoding='utf8')
        reviewed=Path(self.temp.name)/'reviewed'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review(['--corpus',str(src),'--out-dir',str(reviewed),'--model','test-model',
                '--base-url','https://example.invalid/v1','--max-output-tokens','128','--quote',str(qp),
                '--token-counts',str(tp),'--runtime-budget',str(bp),'--max-calls','1']),0)
        args.extend(['--runtime-contract-file',str(reviewed/'runtime-contract.json')])
        from test_cost_group import group_cli_args
        args.extend(group_cli_args(Path(self.temp.name), json.loads((reviewed/'runtime-contract.json').read_text(encoding='utf8'))))
        with patch.dict('os.environ',{'OKB_FACT_JUDGE_API_KEY':'synthetic'}),patch.object(AuditedFacts,'_exchange',return_value=self.envelope),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args),0)
            self.assertEqual(recover(['--journal',str(out/'call-journal.jsonl'),'--model','test-model','--out',str(rp)]),0)
            self.assertEqual(main(['--corpus',str(src),'--out-dir',str(Path(self.temp.name)/'replay'),
                                   '--replay',str(rp),'--model','test-model']),0)
        summary=json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['call_audit']['estimated_known_cost'],'0.00028')
        self.assertFalse(summary['semantic_reliability_validated'])
    def test_live_cli_requires_cost_and_output_options(self):
        from semantic_robustness import main
        from score_four_layer import main as score
        out=Path(self.temp.name)/'denied'
        with patch('urllib.request.build_opener') as http,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            common=['--out-dir',str(out),'--live','--authorize-paid-calls','--model','test-model']
            self.assertEqual(main(common),2)
            self.assertEqual(score(common+['--paper','missing','--plan','missing','--run-dir','missing']),2)
            http.assert_not_called()
        self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
