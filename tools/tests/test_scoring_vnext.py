"""S1/S2 semantic contracts, routing truth table and legacy compatibility."""
import copy
import itertools
import json
from pathlib import Path
import sys
import unittest
import contextlib
import io
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools'),str(Path(__file__).parent)]
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError
from scoring.observation_contract import validate_receipt, dependency_status
from scoring.outcome_v2 import aggregate, project_row
from jsonschema import validate, ValidationError
import test_four_layer as legacy
plan=legacy.plan
FakeJudge=legacy.FakeJudge


def receipt():
    return dict(version='okb-observation-receipt-v1-draft',run_id='r',task_id='t',instance_id='i',
        scope_id='memory',storage_kind='api',window='after',state='complete_empty',method='enumerate',
        collector_version='test',started_at='2026-09-29T00:00:00Z',completed_at='2026-09-29T00:00:01Z',
        enumeration_complete=True,instance_verified=True,control_passed=True,control_cleaned=True,
        item_count=0,evidence_refs=['e1'],control_refs=['control'],errors=[])


class ObservationTests(unittest.TestCase):
    def validate(self,r):
        return validate_receipt(r,run_id='r',task_id='t',instance_id='i',evidence_ids=['e1','control'])
    def test_valid_empty_api_needs_no_fake_file(self):
        r=self.validate(receipt())
        self.assertTrue(dependency_status(r,dict(scope_id='memory',window='after'))['sufficient'])
    def test_nonempty_and_empty_counts(self):
        for state,n in [('complete_nonempty',0),('complete_empty',1)]:
            r=receipt();r.update(state=state,item_count=n)
            with self.assertRaises(ValueError):self.validate(r)
        r=receipt();r.update(state='complete_nonempty',item_count=1);self.validate(r)
    def test_no_default_complete_for_legacy_counts(self):
        with self.assertRaises(ValidationError):self.validate({'files_scanned':0})
    def test_proofs_required(self):
        for k in ['enumeration_complete','instance_verified','control_passed','control_cleaned']:
            r=receipt();r[k]=False
            with self.assertRaises(ValueError):self.validate(r)
        for k,v in [('errors',['permission denied']),('control_refs',[]),('item_count',None)]:
            r=receipt();r[k]=v
            with self.assertRaises(ValueError):self.validate(r)
    def test_identity_source_and_time_binding(self):
        for k,v in [('instance_id','other'),('run_id','other'),('task_id','other'),
                    ('evidence_refs',['invented']),('completed_at','2026-09-28T00:00:00Z')]:
            r=receipt();r[k]=v
            with self.assertRaises(ValueError):self.validate(r)
    def test_partial_and_missing_are_insufficient(self):
        for s in ['partial','unavailable','error']:
            r=receipt();r['state']=s;self.validate(r)
            self.assertFalse(dependency_status(r,dict(scope_id='memory',window='after'))['sufficient'])
    def test_snapshot_cannot_prove_whole_task(self):
        self.assertFalse(dependency_status(receipt(),dict(scope_id='memory',window='whole_task'))['sufficient'])
        self.assertFalse(dependency_status(receipt(),dict(scope_id='other',window='after'))['sufficient'])


class OutcomeTests(unittest.TestCase):
    def vector(self,states):
        return [dict(id=str(i),result=s,on_failure=['omitted','confused','wrongly_reused'][i]) for i,s in enumerate(states)]
    def test_exhaustive_three_requirement_truth_table(self):
        for states in itertools.product(['supported','contradicted','insufficient'],repeat=3):
            r=aggregate(True,self.vector(states))
            expected='fail' if 'contradicted' in states else 'unassessable' if 'insufficient' in states else 'pass'
            self.assertEqual(r['task_outcome'],expected,states)
            self.assertEqual(r['score'],{'pass':1,'fail':0,'unassessable':None}[expected])
            if expected=='fail':
                first=states.index('contradicted')
                self.assertEqual(r['classification_status']=='resolved','insufficient' not in states[:first])
    def test_invalid_source_overrides_every_vector(self):
        r=aggregate(False,self.vector(['contradicted','supported','insufficient']))
        self.assertEqual(r['task_outcome'],'unassessable');self.assertIsNone(r['score'])
    def test_empty_and_duplicate_checks_rejected(self):
        with self.assertRaises(ValueError):aggregate(True,[])
        c=self.vector(['supported'])[0]
        with self.assertRaises(ValueError):aggregate(True,[c,c])
    def test_version_one_still_abstains_v2_proven_failure(self):
        p=plan()['samples'][0]
        p['checks'].append(dict(id='file',layer='objective',op='file_exists',path='missing.txt',on_failure='confused',basis='required'))
        r=legacy.LayersTests().run_case(p=p)
        original=copy.deepcopy(r)
        self.assertIsNone(r['score'])
        v2=project_row(r,p['checks'])
        self.assertEqual(v2['score'],0);self.assertIsNone(v2['error_class'])
        self.assertFalse(v2['legacy_report_compatible']);self.assertEqual(r,original)
        schema=json.loads((ROOT/'schemas/task-outcome.v2.schema.json').read_text())
        validate(v2,schema)
        for score in [1,None]:
            bad=dict(v2,score=score)
            with self.assertRaises(ValidationError):validate(bad,schema)
    def test_priority_uses_plan_order_not_layer_order(self):
        p=plan()['samples'][0]
        p['checks'].append(dict(id='file',layer='objective',op='file_exists',path='missing.txt',on_failure='confused',basis='required'))
        r=legacy.LayersTests().run_case(p=p,j=FakeJudge('contradicted'))
        self.assertEqual(project_row(r,p['checks'])['error_class'],'omitted')


class OutcomeCliTests(unittest.TestCase):
    def test_prepare_cli_v1_v2_and_result_schema(self):
        import hashlib
        import yaml
        from simulate import simulate
        from score_four_layer import main
        sample=dict(id='vnext_fixture',dimension='long_term_retention',meta={'suite_version':'v0.2'},
            score_policy='rule_only',expect={'verdict':'remembered'},
            script=[dict(session='s1',phase='write',turns=[{'text':'Remember peanut allergy.'}]),
                    dict(session='s2',phase='probe',turns=[{'text':'Write missing.json.'}])],
            anchors=[dict(id='A1',type='memory_entry',key='*',value_regex='peanut',expect='present',severity='hard',on_violation='omitted'),
                     dict(id='A2',type='file_content',path='missing.json',expect='present',severity='hard',on_violation='omitted')])
        policy=dict(policy_version=1,tier='remember',on_missing_record='fail',defaults={'reply_text':'ok'},
            samples=[dict(sample_id=sample['id'],turns=[
                dict(match={'session':'s1','turn':1},memory_ops=[{'op':'set','key':'allergy','value':'User has peanut allergy.'}]),
                dict(match={'session':'s2','turn':1},file_ops=[{'op':'write','path':'other.json','content':'{}'}])])])
        with testtmp.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
            root=Path(td);paper=root/'paper.jsonl';paper.write_text(json.dumps(sample)+'\n',encoding='utf8')
            pol=root/'policy.yaml';pol.write_text(yaml.safe_dump(policy),encoding='utf8')
            rd,_=simulate(str(paper),str(pol),'vnext-test',str(root/'runs'))
            p=plan();p['paper_sha256']=hashlib.sha256(paper.read_bytes()).hexdigest()
            row=p['samples'][0];row['sample_id']=sample['id']
            row['checks'].append(dict(id='exists',layer='objective',op='file_exists',path='missing.json',on_failure='omitted',basis='s2.t1'))
            row['anchor_disposition']['A2']=dict(role='objective',checks=['exists'],reason='required file')
            pp=root/'plan.json';pp.write_text(json.dumps(p),encoding='utf8')
            args=['--paper',str(paper),'--plan',str(pp),'--run-dir',str(rd)]
            self.assertEqual(main(args+['--out-dir',str(root/'v1')]),1)
            self.assertEqual(main(args+['--out-dir',str(root/'v2'),'--outcome-version','v2']),0)
            old=json.loads((root/'v1/results.json').read_text())[0]
            new=json.loads((root/'v2/results.json').read_text())[0]
            self.assertIsNone(old['score']);self.assertEqual(new['score'],0)
            self.assertIsNone(new['error_class']);self.assertEqual(new['comparison'],'not_comparable')
            self.assertEqual(old['semantic_requests'],new['semantic_requests'])
            schema=json.loads((ROOT/'schemas/task-outcome.v2.schema.json').read_text())
            validate(new,schema)
            summary=json.loads((root/'v2/summary.json').read_text())
            self.assertEqual(summary['task_outcomes'],{'fail':1})
            self.assertEqual(summary['classification_resolved'],0)


if __name__=='__main__':unittest.main()
