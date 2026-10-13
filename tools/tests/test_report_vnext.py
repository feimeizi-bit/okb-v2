"""Diagnostic report accounting, source binding and inert HTML tests."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tools'),str(Path(__file__).parent)]
from scoring.report_v2 import load_bundle,build_report,render_html,metric
from report_four_layer import main
from scoring.outcome_v2 import aggregate
from test_four_layer import plan as legacy_plan


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.run=self.root/'run';self.run.mkdir()
        self.score=self.root/'score';self.score.mkdir()
        self.paper=self.root/'paper.jsonl';self.plan=self.root/'plan.json'
        self.sid='sample';self.agents=['a','b']
        self.sample=dict(id=self.sid,dimension='long_term_retention',script=[dict(session='s1',turns=[dict(text='Write required file.')])],anchors=[dict(id='A1')])
        self.paper.write_text(json.dumps(self.sample)+'\n',encoding='utf8')
        self.policy=legacy_plan();self.policy['paper_sha256']=self.hash(self.paper)
        self.policy['samples']=[dict(sample_id=self.sid,anchor_disposition={'A1':dict(role='mixed',checks=['memory','file'],reason='task')},checks=[
            dict(id='memory',layer='semantic',scope='memory_after',requirement='Persist fact.',on_failure='omitted',basis='s1.t1'),
            dict(id='file',layer='objective',op='file_exists',path='needed.txt',on_failure='confused',basis='s1.t1')])]
        self.write(self.plan,self.policy)
        self.manifest=dict(run_id='test-run',agents=[dict(name=a,agent_kind='mock') for a in self.agents],tasks=[dict(task_id=a+'-'+self.sid,status='ok',duration_ms=3) for a in self.agents])
        self.write(self.run/'manifest.json',self.manifest)
        (self.run/'evidence.jsonl').write_text('',encoding='utf8')
        self.rows=[self.row(a) for a in self.agents]
        self.freeze()

    def tearDown(self):self.temp.cleanup()
    def hash(self,p):return hashlib.sha256(p.read_bytes()).hexdigest()
    def write(self,p,value):p.write_text(json.dumps(value),encoding='utf8')
    def row(self,a):
        checks=[dict(id='memory',layer='semantic',on_failure='omitted',result='insufficient',rationale='pending',evidence_refs=[]),dict(id='file',layer='objective',on_failure='confused',result='contradicted',rationale='missing',evidence_refs=[])]
        axes=aggregate(True,checks)
        return dict(version='okb-four-layer-v2',task_id=a+'-'+self.sid,sample_id=self.sid,plan_sha256=self.hash(self.plan),legacy_report_compatible=False,status='assessed',verdict=None,layer1={'valid':True},layer2=[checks[1]],layer3=[checks[0]],evidence_refs=[],comparison='not_comparable',**axes)
    def freeze(self):
        lock=dict(format='okb-four-layer-v2',plan_sha256=self.hash(self.plan),sha256={str(p.resolve()):self.hash(p) for p in [self.paper,self.plan,*self.run.iterdir()]})
        self.write(self.score/'input-lock.json',lock)
        self.save_results()
    def save_results(self):
        from collections import Counter
        self.summary=dict(format='okb-four-layer-v2',mode='prepare',inputs_unchanged=True,release_authorized=False,semantic_reliability_validated=False,tasks=len(self.rows),evidence_valid=sum(r['layer1']['valid'] for r in self.rows),assessed=sum(r['status']=='assessed' for r in self.rows),unassessable=sum(r['status']=='unassessable' for r in self.rows),task_outcomes=dict(Counter(r['task_outcome'] for r in self.rows)),classification_resolved=sum(r['classification_status']=='resolved' for r in self.rows))
        self.write(self.score/'results.json',self.rows);self.write(self.score/'summary.json',self.summary);self.complete()
    def complete(self):self.write(self.score/'complete.json',{n+'_sha256':self.hash(self.score/(n+'.json')) for n in ('results','summary')})
    def load(self):return load_bundle(self.score,self.run,self.paper,self.plan)[0]
    def reject(self):
        with self.assertRaises((ValueError,KeyError,TypeError)):self.load()
    def args(self):return ['--score-dir',str(self.score),'--run-dir',str(self.run),'--paper',str(self.paper),'--plan',str(self.plan),'--out-dir',str(self.root/'report')]

    def test_proven_failure_unresolved_class_visible(self):
        report=build_report(self.load());m=report['dimensions']['long_term_retention']['agents']['a']
        self.assertEqual(m['fail'],1);self.assertEqual(m['unresolved_classification'],1)
        self.assertEqual(m['assessed_task_pass_rate'],0);self.assertEqual(m['error_classes'],{})
        self.assertIsNone(report['overall_score']);self.assertIsNone(report['ranking'])
        self.assertEqual(len(report['review_tasks']),2)
    def test_missing_dimension_is_null(self):
        m=build_report(self.load())['dimensions']['dynamic_update']['agents']['a']
        self.assertIsNone(m['sample_coverage']);self.assertIsNone(m['assessed_task_pass_rate'])
    def test_common_subset_and_exclusion_keep_both_agents(self):
        r=self.rows[1];r.update(aggregate(False,[]),status='unassessable',layer1={'valid':False,'reason':'missing'},layer2=[],layer3=[])
        self.save_results();d=build_report(self.load())['dimensions']['long_term_retention']
        self.assertEqual(d['common_assessable_units'],[])
        self.assertEqual(d['common_excluded'][0]['unavailable_agents'],['b'])
        self.assertEqual(d['agents']['b']['unassessable'],1)
    def test_repeats_do_not_inflate_sample_count(self):
        t=build_report(self.load())['tasks'][0]
        u=copy.deepcopy(t);u.update(repeat='-r2',task_outcome='unassessable',score=None)
        m=metric([t,u],1)
        self.assertEqual(m['planned_tasks'],2);self.assertEqual(m['planned_samples'],1)
        self.assertEqual(m['fully_assessable_samples'],0);self.assertIsNone(m['complete_sample_mean'])
    def test_declared_agent_without_selected_tasks_is_visible(self):
        self.manifest['tasks']=self.manifest['tasks'][:1]
        self.rows=self.rows[:1]
        self.write(self.run/'manifest.json',self.manifest);self.freeze()
        report=build_report(self.load());d=report['dimensions']['long_term_retention']
        self.assertEqual(len(report['agents']),2)
        self.assertEqual(d['missing_sample_tasks']['b'],[self.sid])
        self.assertEqual(d['agents']['b']['sample_coverage'],0)
        self.assertIsNone(d['agents']['b']['assessed_task_pass_rate'])
    def test_cross_task_evidence_rejected(self):
        e=dict(id='e1',run_id='test-run',task_id='b-'+self.sid,source='system',type='message',payload={})
        (self.run/'evidence.jsonl').write_text(json.dumps(e)+'\n',encoding='utf8')
        self.rows[0]['evidence_refs']=['e1'];self.freeze();self.reject()
    def test_completion_required(self):
        (self.score/'complete.json').unlink()
        with self.assertRaises(OSError):self.load()
    def test_result_fingerprint(self):
        self.write(self.score/'results.json',[]);self.reject()
    def test_summary_recomputed(self):
        self.summary['assessed']=0;self.write(self.score/'summary.json',self.summary);self.complete();self.reject()
    def test_input_drift(self):
        self.paper.write_text('{}\n',encoding='utf8');self.reject()
    def test_extra_run_file(self):
        (self.run/'extra').write_text('x');self.reject()
    def test_duplicate_json_keys(self):
        p=self.score/'summary.json';p.write_text('{"format":1,"format":2}',encoding='utf8');self.complete();self.reject()
    def test_missing_result_task(self):
        self.rows.pop();self.save_results();self.reject()
    def test_duplicate_result_task(self):
        self.rows.append(copy.deepcopy(self.rows[0]));self.save_results();self.reject()
    def test_task_identity_mismatch(self):
        self.rows[0]['sample_id']='other';self.save_results();self.reject()
    def test_check_omission(self):
        self.rows[0]['layer3']=[];self.save_results();self.reject()
    def test_duplicate_check(self):
        self.rows[0]['layer2']*=2;self.save_results();self.reject()
    def test_failure_cannot_be_reported_pass(self):
        self.rows[0].update(task_outcome='pass',score=1,error_class='remembered');self.save_results();self.reject()
    def test_boolean_score_rejected(self):
        self.rows[0]['score']=False;self.save_results();self.reject()
    def test_prepare_semantic_verdict_rejected(self):
        r=self.rows[0];r['layer3'][0]['result']='supported'
        r.update(aggregate(True,r['layer3']+r['layer2']));r['verdict']=r['error_class']
        self.save_results();self.reject()
    def test_broken_evidence(self):
        self.rows[0]['evidence_refs']=['invented'];self.save_results();self.reject()
    def test_wrong_plan_fingerprint(self):
        self.rows[0]['plan_sha256']='0'*64;self.save_results();self.reject()
    def test_legacy_rejected(self):
        self.summary['format']='okb-four-layer-v1';self.write(self.score/'summary.json',self.summary);self.complete();self.reject()
    def test_html_inert(self):
        report=build_report(self.load());attack='</pre><script>alert(1)</script><img src="https://example.invalid">'
        report['tasks'][0]['script']=attack
        out=render_html(report)
        self.assertNotIn('<script>',out);self.assertNotIn('<img ',out)
        self.assertIn('&lt;script&gt;',out);self.assertIn('default-src',out)
        self.assertIn('href="#task-0"',out)
    def test_cli_write_and_refuse_overwrite(self):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(self.args()),0);self.assertEqual(main(self.args()),2)
        complete=json.loads((self.root/'report/complete.json').read_text())
        for name,digest in complete.items():self.assertEqual(self.hash(self.root/'report'/name),digest)
    def test_cli_no_output_on_rejection(self):
        self.rows.pop();self.save_results()
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(self.args()),2)
        self.assertFalse((self.root/'report').exists())
    def test_dispatcher(self):
        from okb.cli import main as cli
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli(['report-vnext']+self.args()),0)


if __name__=='__main__':unittest.main()
