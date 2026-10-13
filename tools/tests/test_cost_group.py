import contextlib
import copy
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))

from scoring.audited_facts import AuditedFacts, read_journal
from scoring.cost_group import CostGroup, cost_group, initialize_ledger, validate_group
from scoring.cost_review import inventory
from scoring.cost_runtime import runtime_contract
from scoring.fact_judge import FactError, canonical, fingerprint, request


def group_cli_args(directory, contract):
    policy = cost_group([('fixture', contract)], group_id='synthetic-group',
        currency=contract['budget']['currency'], max_total_cost=contract['reserved_batch_cost'],
        ledger_path=Path(directory)/'group-ledger.sqlite3')
    initialize_ledger(policy)
    path = Path(directory)/'cost-group.json'
    path.write_text(canonical(policy), encoding='utf8')
    return ['--cost-group-file', str(path), '--cost-scope', 'fixture']


class CostGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.req = request('synthetic fact', {}, [dict(evidence_id='e1', content=dict(value='fixture'))])
        self.options = dict(model='fixture-model', base_url='https://example.invalid/v1', max_output_tokens=128)
        rows = [dict(request=self.req, request_sha256=fingerprint(self.req), source=dict(case_id='fixture'))]
        inv = inventory(rows, **self.options)
        quote = dict(format='okb-judge-price-quote-v1-draft', model='fixture-model',
            endpoint_sha256=inv['endpoint_sha256'], currency='USD', input_per_million='2',
            output_per_million='3', price_source='synthetic-test-only', observed_on='2026-09-30')
        tokens = dict(format='okb-judge-token-counts-v1-draft', model='fixture-model',
            endpoint_sha256=inv['endpoint_sha256'], protocol=inv['protocol'],
            request_set_sha256=inv['request_set_sha256'], method='synthetic', source='synthetic-test-only',
            records=[dict(wire_request_sha256=inv['requests'][0]['wire_request_sha256'], input_tokens=100)])
        self.budget = dict(currency='USD', input_per_million='2', output_per_million='3',
            price_source='synthetic-test-only', max_total_cost='0.002', reserve_per_request='0.001')
        self.contract = runtime_contract(inv, quote, tokens, self.budget, 1)
        self.rows = rows
        self.policy = cost_group([('left', self.contract), ('right', self.contract)],
            group_id='synthetic', currency='USD', max_total_cost='0.002', ledger_path=self.root/'ledger.sqlite3')
        initialize_ledger(self.policy)
        self.group = CostGroup(self.policy)

    def client(self, scope='left', name='calls.jsonl'):
        return AuditedFacts(journal_path=self.root/name, budget=self.budget, max_calls=1,
            api_key='synthetic', runtime_contract=self.contract, reviewed_requests=self.rows,
            cost_group=CostGroup(self.policy), cost_scope=scope, **self.options)

    def envelope(self, usage=None, content=None):
        response = dict(result='supported', rationale='synthetic fixture',
            citations=[dict(evidence_id='e1', pointer='/value', quote='fixture')])
        return dict(model='fixture-model', usage=usage if usage is not None else dict(prompt_tokens=100, completion_tokens=20),
            choices=[dict(finish_reason='stop', message=dict(content=content if content is not None else canonical(response)))])

    def test_initial_state_is_not_approval_or_remote_cap(self):
        summary = self.group.summary()
        self.assertFalse(summary['halted'])
        self.assertEqual(summary['attempted_calls'], 0)
        self.assertFalse(summary['hard_monetary_cap'])
        self.assertFalse(summary['human_approval_verified'])

    def test_exact_scope_contract_required(self):
        changed = copy.deepcopy(self.contract)
        changed['budget']['reserve_per_request'] = '0.002'
        with self.assertRaises(ValueError): self.group.validate_scope('left', changed)
        with self.assertRaises(ValueError): self.group.validate_scope('absent', self.contract)

    def test_all_scopes_reserved_before_minting_group(self):
        with self.assertRaises(ValueError):
            cost_group([('left', self.contract), ('right', self.contract)], group_id='synthetic',
                currency='USD', max_total_cost='0.001', ledger_path=self.root/'new.sqlite3')

    def test_currency_cannot_mix(self):
        with self.assertRaises(ValueError):
            cost_group([('left', self.contract)], group_id='synthetic', currency='CNY',
                max_total_cost='1', ledger_path=self.root/'new.sqlite3')

    def test_duplicate_scopes_denied(self):
        with self.assertRaises(ValueError):
            cost_group([('left', self.contract), ('left', self.contract)], group_id='synthetic',
                currency='USD', max_total_cost='1', ledger_path=self.root/'new.sqlite3')

    def test_group_fingerprint_and_flags_required(self):
        for field,value in [('max_total_cost','10'), ('execution_authorized',True), ('hard_monetary_cap',True)]:
            policy = dict(self.policy, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError): validate_group(policy)

    def test_missing_ledger_not_silently_recreated(self):
        Path(self.policy['ledger_path']).unlink()
        with self.assertRaises(ValueError): CostGroup(self.policy)
        self.assertFalse(Path(self.policy['ledger_path']).exists())

    def test_initialization_never_overwrites(self):
        with self.assertRaises(FileExistsError): initialize_ledger(self.policy)
        self.assertEqual(self.group.summary()['attempted_calls'], 0)

    def test_changed_path_cannot_reuse_bound_ledger(self):
        policy = dict(self.policy, group_id='other')
        policy['group_sha256'] = fingerprint({key:value for key,value in policy.items() if key!='group_sha256'})
        with self.assertRaises(ValueError): CostGroup(policy)

    def test_pending_blocks_other_client_and_scope(self):
        self.group.reserve('left', fingerprint(self.req))
        other = CostGroup(self.policy)
        with self.assertRaises(ValueError): other.reserve('right', fingerprint(self.req))
        self.assertEqual(other.summary()['pending_calls'], 1)

    def test_known_cost_keeps_conservative_reservation(self):
        attempt = self.group.reserve('left', fingerprint(self.req))
        self.group.settle(attempt, '0.0001', batch_halted=False)
        summary = CostGroup(self.policy).summary()
        self.assertEqual(summary['committed_reservation_cost'], '0.001')
        self.assertEqual(summary['estimated_known_cost'], '0.0001')
        self.assertFalse(summary['halted'])
        self.group.reserve('right', fingerprint(self.req))

    def test_new_journal_cannot_repeat_same_scope_request(self):
        attempt = self.group.reserve('left', fingerprint(self.req))
        self.group.settle(attempt, '0', batch_halted=False)
        with self.assertRaises(ValueError): CostGroup(self.policy).reserve('left', fingerprint(self.req))

    def test_unknown_blocks_entire_group_and_cannot_be_resettled(self):
        attempt = self.group.reserve('left', fingerprint(self.req))
        self.group.settle(attempt, None, batch_halted=True)
        with self.assertRaises(ValueError): CostGroup(self.policy).reserve('right', fingerprint(self.req))
        with self.assertRaises(ValueError): self.group.settle(attempt, '0', batch_halted=False)
        summary = self.group.summary()
        self.assertEqual(summary['unknown_calls'], 1)
        self.assertEqual(summary['committed_reservation_cost'], '0.001')

    def test_over_reservation_cost_recorded_and_halts(self):
        attempt = self.group.reserve('left', fingerprint(self.req))
        self.group.settle(attempt, '0.003', batch_halted=False)
        summary = self.group.summary()
        self.assertEqual(summary['estimated_known_cost'], '0.003')
        self.assertEqual(summary['remaining_reservation_capacity'], '0')
        self.assertTrue(summary['halted'])

    def test_halt_flag_propagates_even_with_known_low_cost(self):
        attempt = self.group.reserve('left', fingerprint(self.req))
        self.group.settle(attempt, '0.0001', batch_halted=True)
        with self.assertRaises(ValueError): self.group.reserve('right', fingerprint(self.req))

    def test_unknown_request_and_invalid_settlement_do_not_mutate(self):
        with self.assertRaises(ValueError): self.group.reserve('left', '0'*64)
        with self.assertRaises(ValueError): self.group.settle('0'*64, '0', batch_halted=False)
        self.assertEqual(self.group.summary()['attempted_calls'], 0)

    def test_ledger_update_and_delete_protected(self):
        self.group.reserve('left', fingerprint(self.req))
        with contextlib.closing(sqlite3.connect(self.policy['ledger_path'])) as connection:
            with self.assertRaises(sqlite3.IntegrityError): connection.execute('DELETE FROM events')
            with self.assertRaises(sqlite3.IntegrityError): connection.execute("UPDATE events SET sha256='wrong'")

    def test_corrupt_chain_denied(self):
        self.group.reserve('left', fingerprint(self.req))
        with contextlib.closing(sqlite3.connect(self.policy['ledger_path'])) as connection:
            connection.execute('DROP TRIGGER no_event_update')
            connection.execute("UPDATE events SET sha256='wrong'")
            connection.commit()
        with self.assertRaises(ValueError): self.group.summary()

    def test_two_processes_share_serial_reservation(self):
        policy_path = self.root/'policy.json'
        policy_path.write_text(canonical(self.policy), encoding='utf8')
        program = "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from scoring.cost_group import CostGroup; group=CostGroup(json.loads(Path(sys.argv[2]).read_text()));\ntry: group.reserve(sys.argv[3],sys.argv[4])\nexcept ValueError: sys.exit(3)"
        processes = [subprocess.Popen([sys.executable, '-c', program, str(ROOT/'tools'), str(policy_path), scope, fingerprint(self.req)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE) for scope in ('left','right')]
        for process in processes: process.communicate(timeout=20)
        self.assertEqual(sorted(process.returncode for process in processes), [0,3])
        self.assertEqual(self.group.summary()['attempted_calls'], 1)

    def test_transport_unknown_blocks_preexisting_other_client(self):
        left = self.client()
        right = self.client('right', 'right.jsonl')
        with patch.object(left, '_exchange', side_effect=RuntimeError('synthetic')) as first:
            with self.assertRaises(FactError): left.judge(self.req)
            first.assert_called_once()
        with patch.object(right, '_exchange') as second:
            with self.assertRaises(FactError): right.judge(self.req)
            second.assert_not_called()
        self.assertEqual(self.group.summary()['unknown_calls'], 1)

    def test_success_records_group_binding_and_known_cost(self):
        client = self.client()
        with patch.object(client, '_exchange', return_value=self.envelope()): client.judge(self.req)
        summary = client.audit_summary()['cost_group']
        self.assertEqual(summary['estimated_known_cost'], '0.00026')
        self.assertFalse(summary['halted'])
        header = read_journal(self.root/'calls.jsonl')['batch']
        self.assertEqual(header['cost_group_binding']['group_sha256'], self.policy['group_sha256'])

    def test_new_output_cannot_repeat_completed_request(self):
        first = self.client()
        with patch.object(first, '_exchange', return_value=self.envelope()): first.judge(self.req)
        second = self.client(name='new-output.jsonl')
        with patch.object(second, '_exchange') as transport:
            with self.assertRaises(FactError): second.judge(self.req)
            transport.assert_not_called()
        self.assertEqual(self.group.summary()['attempted_calls'], 1)

    def test_unknown_denies_other_model_and_endpoint_before_new_journal(self):
        options = dict(self.options, model='other-model', base_url='https://other.invalid/v1')
        inv = inventory(self.rows, **options)
        quote = dict(self.contract['quote'], model='other-model', endpoint_sha256=inv['endpoint_sha256'])
        tokens = dict(self.contract['token_counts'], model='other-model', endpoint_sha256=inv['endpoint_sha256'],
            request_set_sha256=inv['request_set_sha256'], records=[dict(wire_request_sha256=inv['requests'][0]['wire_request_sha256'],input_tokens=100)])
        contract = runtime_contract(inv, quote, tokens, self.budget, 1)
        policy = cost_group([('left',self.contract),('right',contract)], group_id='two-models',
            currency='USD', max_total_cost='0.002', ledger_path=self.root/'two-models.sqlite3')
        initialize_ledger(policy)
        group = CostGroup(policy)
        attempt = group.reserve('left', fingerprint(self.req))
        group.settle(attempt, None, batch_halted=True)
        with self.assertRaises(FactError):
            AuditedFacts(journal_path=self.root/'other-model.jsonl', budget=self.budget,
                max_calls=1, api_key='synthetic', runtime_contract=contract, reviewed_requests=self.rows,
                cost_group=group, cost_scope='right', **options)
        self.assertFalse((self.root/'other-model.jsonl').exists())

    def test_output_limit_halts_all_scopes_even_below_reservation(self):
        client = self.client()
        with patch.object(client, '_exchange', return_value=self.envelope(usage=dict(prompt_tokens=100,completion_tokens=129))):
            client.judge(self.req)
        self.assertTrue(self.group.summary()['halted'])
        with self.assertRaises(FactError): self.group.reserve('right', fingerprint(self.req))

    def test_settlement_failure_remains_pending_without_resend(self):
        client = self.client()
        with patch.object(client, '_exchange', return_value=self.envelope()) as transport, patch.object(client.cost_group, 'settle', side_effect=FactError('synthetic persistence failure')):
            with self.assertRaises(FactError): client.judge(self.req)
            with self.assertRaises(FactError): client.judge(self.req)
            transport.assert_called_once()
        self.assertEqual(self.group.summary()['pending_calls'], 1)

    def test_journal_persistence_failure_leaves_group_pending(self):
        client = self.client()
        with patch.object(client, '_append', side_effect=FactError('synthetic persistence failure')), patch.object(client, '_exchange') as transport:
            with self.assertRaises(FactError): client.judge(self.req)
            transport.assert_not_called()
        self.assertEqual(self.group.summary()['pending_calls'], 1)

    def test_invalid_output_keeps_known_consumption(self):
        client = self.client()
        with patch.object(client, '_exchange', return_value=self.envelope(content='invalid')):
            with self.assertRaises(FactError): client.judge(self.req)
        self.assertEqual(self.group.summary()['estimated_known_cost'], '0.00026')
        self.assertEqual(self.group.summary()['unknown_calls'], 0)

    def test_missing_usage_blocks_other_scope(self):
        client = self.client()
        with patch.object(client, '_exchange', return_value=self.envelope(usage={'invalid':None})): client.judge(self.req)
        with self.assertRaises(ValueError): self.group.reserve('right', fingerprint(self.req))

    def test_live_cli_requires_group_before_output_or_http(self):
        from semantic_robustness import main
        from score_four_layer import main as score
        budget_path = self.root/'budget.json'
        contract_path = self.root/'contract.json'
        budget_path.write_text(canonical(self.budget), encoding='utf8')
        contract_path.write_text(canonical(self.contract), encoding='utf8')
        out = self.root/'denied'
        args = ['--out-dir',str(out),'--live','--authorize-paid-calls','--model','fixture-model',
            '--base-url',self.options['base_url'],'--max-calls','1','--max-output-tokens','128',
            '--budget-file',str(budget_path),'--runtime-contract-file',str(contract_path)]
        with patch('urllib.request.build_opener') as http, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 2)
            self.assertEqual(score(args+['--paper','missing','--plan','missing','--run-dir','missing']), 2)
            http.assert_not_called()
        self.assertFalse(out.exists())

    def test_review_cli_makes_declared_group_not_approval(self):
        from review_cost_group import main
        contract_path = self.root/'contract.json'
        contract_path.write_text(canonical(self.contract), encoding='utf8')
        out = self.root/'review'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--scope','fixture='+str(contract_path),'--group-id','synthetic',
                '--currency','USD','--max-total-cost','0.001','--out-dir',str(out)]), 0)
        report = json.loads((out/'review.json').read_text(encoding='utf8'))
        self.assertFalse(report['execution_authorized'])
        self.assertTrue(report['mutable_ledger_excluded_from_complete'])
        self.assertEqual(len(json.loads((out/'complete.json').read_text())), 2)


if __name__ == '__main__':
    unittest.main()
