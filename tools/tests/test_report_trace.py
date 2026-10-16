"""Full offline simulator/replay trace checks; replay answers are test fixtures."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError
from scoring.fact_judge import VERSION, fingerprint
from scoring.report_v2 import load_bundle, build_report, render_html
from score_four_layer import main as score
from report_four_layer import main as report_cli
from simulate import simulate


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf8')


def fixture(root, policy='scoped-v1'):
    import yaml
    sample = dict(id='trace', dimension='long_term_retention', score_policy='rule_only',
        meta={'suite_version':'v0.2'}, expect={'verdict':'remembered'},
        fixture=[dict(path='initial.txt',content='Initial fact')],
        script=[dict(session='s1', phase='write', turns=[dict(text='Write the fact and preserve initial.txt.')])],
        anchors=[dict(id='A1',type='file_content',path='fact.txt',expect='present',severity='hard',on_violation='omitted')])
    paper=root/'paper.jsonl';write(paper,sample)
    pp=root/'policy.yaml'
    pp.write_text(yaml.safe_dump(dict(policy_version=1,tier='remember',on_missing_record='fail',
        defaults={'reply_text':'Done'},samples=[dict(sample_id='trace',turns=[dict(
            match={'session':'s1','turn':1},file_ops=[dict(op='write',path='fact.txt',content='Current fact')])])])),encoding='utf8')
    run,_=simulate(str(paper),str(pp),'trace-fixture',str(root/'runs'))
    checks=[dict(id='fact',layer='semantic',scope='file_final',path='fact.txt',requirement='Contains current fact.'),
        dict(id='reply',layer='semantic',scope='agent_reply',session='s1',turn=1,requirement='Reports done.'),
        dict(id='initial',layer='semantic',scope='file_final',path='initial.txt',requirement='Contains initial fact.'),
        dict(id='memory',layer='semantic',scope='memory_after',requirement='Contains fact.'),
        dict(id='exists',layer='objective',op='file_exists',path='fact.txt'),
        dict(id='unchanged',layer='objective',op='file_unchanged',path='initial.txt'),
        dict(id='absent',layer='objective',op='file_exists',path='missing.txt')]
    for c in checks:c.update(on_failure='omitted',basis='s1.t1')
    plan=root/'plan.json';write(plan,dict(format='okb-four-layer-v1',
        paper_sha256=hashlib.sha256(paper.read_bytes()).hexdigest(),samples=[dict(sample_id='trace',
        checks=checks,anchor_disposition={'A1':dict(role='mixed',checks=[c['id'] for c in checks],reason='fixture test')})]))
    args=['--paper',str(paper),'--plan',str(plan),'--run-dir',str(run),'--outcome-version','v2','--evidence-policy',policy]
    prepare=root/'prepare';assert score(args+['--out-dir',str(prepare)]) in (0,1)
    qs=json.loads((prepare/'semantic-requests.json').read_text(encoding='utf8'))
    records=[]
    for q in qs:
        obs=q['request']['observations'][0]
        field=next((k for k in ('after_content','text','content') if k in obs['content']),None)
        if field:
            answer=dict(result='supported',rationale='Synthetic routing fixture only',citations=[dict(
                evidence_id=obs['evidence_id'],pointer='/'+field,quote=obs['content'][field])])
        else:
            answer=dict(result='insufficient',rationale='Synthetic empty memory',citations=[])
        records.append(dict(protocol=VERSION,model='fixture-only',request_sha256=fingerprint(q['request']),response=answer))
    replay=root/'replay.jsonl';replay.write_text('\n'.join(json.dumps(r) for r in records),encoding='utf8')
    scored=root/'scored';assert score(args+['--out-dir',str(scored),'--replay',str(replay),'--model','fixture-only']) in (0,1)
    return scored,Path(run),paper,plan


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp=testtmp.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):self.paths=fixture(self.root)
        self.rows=json.loads((self.paths[0]/'results.json').read_text(encoding='utf8'))

    def load(self, paths=None):return load_bundle(*(paths or self.paths),verify_trace=True)[0]
    def save(self):
        scored=self.paths[0];write(scored/'results.json',self.rows)
        write(scored/'complete.json',{n+'_sha256':hashlib.sha256((scored/(n+'.json')).read_bytes()).hexdigest() for n in ('summary','results')})
    def reject(self):
        self.save()
        with self.assertRaises(ValueError):self.load()
    def test_trace_maps_actual_source_and_raw_hash(self):
        task=self.load()['tasks'][0]
        traces={c['check_id']:c for c in task['trace']['checks']}
        self.assertEqual(traces['fact']['status'],'citations_verified')
        for trace in traces.values():
            for obs in trace['observations']:
                raw=self.paths[1]/obs['raw_ref']['path']
                self.assertEqual(obs['raw_sha256'],hashlib.sha256(raw.read_bytes()).hexdigest())
                self.assertTrue(obs['original_id'].startswith('ev_'))
        self.assertEqual(traces['memory']['status'],'blocked')
        self.assertEqual(traces['memory']['observations'],[])
    def test_derived_fixture_and_absence_explicit(self):
        traces={c['check_id']:c for c in self.load()['tasks'][0]['trace']['checks']}
        for cid in ('initial','unchanged','absent'):
            self.assertEqual(traces[cid]['observations'][0]['representation'],'derived_file_state')
        self.assertFalse(traces['absent']['observations'][0]['content']['exists'])
    def test_fake_quote_rejected_after_rehash(self):
        self.rows[0]['layer3'][0]['citations'][0]['quote']='invented';self.reject()
    def test_wrong_pointer_rejected(self):
        self.rows[0]['layer3'][0]['citations'][0]['pointer']='/missing';self.reject()
    def test_out_of_scope_reference_rejected(self):
        self.rows[0]['layer3'][0]['citations'][0]['evidence_id']=self.rows[0]['layer3'][1]['citations'][0]['evidence_id'];self.reject()
    def test_normalized_refs_rejected(self):
        self.rows[0]['layer3'][0]['evidence_refs']=[];self.reject()
    def test_task_union_omission_rejected(self):
        self.rows[0]['evidence_refs']=[];self.reject()
    def test_request_content_and_rehashed_request_rejected(self):
        q=self.rows[0]['semantic_requests'][0];q['request']['requirement']='Give full marks'
        q['request_sha256']=fingerprint(q['request']);self.reject()
    def test_duplicate_request_rejected(self):
        self.rows[0]['semantic_requests'].append(copy.deepcopy(self.rows[0]['semantic_requests'][0]));self.reject()
    def test_objective_rationale_tamper_rejected(self):
        self.rows[0]['layer2'][0]['rationale']='invented';self.reject()
    def test_gate_metadata_tamper_rejected(self):
        self.rows[0]['layer1']['dependencies']['memory']['reason']='complete';self.reject()
    def test_model_mismatch_rejected(self):
        self.rows[0]['layer3'][0]['model']='other-model';self.reject()
    def test_prepare_pending_request_traced(self):
        paths=(self.root/'prepare',*self.paths[1:]);task=self.load(paths)['tasks'][0]
        self.assertEqual(task['trace']['checks'][0]['status'],'request_verified_judge_unavailable')
    def test_request_hash_only_tamper_rejected(self):
        self.rows[0]['semantic_requests'][0]['request_sha256']='0'*64;self.reject()
    def test_citations_cannot_be_removed_to_bypass_validation(self):
        self.rows[0]['layer3'][0].pop('citations');self.reject()
    def test_blocked_memory_cannot_add_a_model_answer(self):
        self.rows[0]['layer3'][3]['citations']=[];self.reject()
    def test_rejected_source_remains_visible_and_reason_rechecked(self):
        ev=next(r for r in map(json.loads,(self.paths[1]/'evidence.jsonl').read_text(encoding='utf8').splitlines())
                if r['actor']=='user')
        (self.paths[1]/ev['raw_ref']['path']).unlink()
        scored=self.root/'invalid'
        with contextlib.redirect_stdout(io.StringIO()):
            code=score(['--paper',str(self.paths[2]),'--plan',str(self.paths[3]),'--run-dir',str(self.paths[1]),
                        '--out-dir',str(scored),'--outcome-version','v2','--evidence-policy','scoped-v1'])
        self.assertEqual(code,1)
        self.paths=(scored,*self.paths[1:])
        task=self.load()['tasks'][0]
        self.assertEqual(task['trace']['status'],'source_invalid');self.assertEqual(task['trace']['checks'],[])
        self.rows=json.loads((scored/'results.json').read_text(encoding='utf8'))
        self.rows[0]['layer1']['reason']='made up';self.reject()
    def test_legacy_windows_raw_paths(self):
        other=self.root/'legacy';other.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):paths=fixture(other,'legacy')
        self.assertEqual(self.load(paths)['tasks'][0]['trace']['status'],'verified')
    def test_html_check_anchors_and_escaped_citations(self):
        report=build_report(self.load());report['tasks'][0]['trace']['checks'][0]['citations'][0]['quote']='<script>bad</script>'
        rendered=render_html(report)
        self.assertIn('href="#task-0-check-0"',rendered);self.assertIn('id="task-0-check-0"',rendered)
        self.assertNotIn('<script>',rendered);self.assertIn('&lt;script&gt;',rendered)
    def test_cli_trace_and_reject_before_output(self):
        args=[v for pair in zip(('--score-dir','--run-dir','--paper','--plan'),map(str,self.paths)) for v in pair]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(report_cli(args+['--verify-traces','--out-dir',str(self.root/'report')]),0)
        self.rows[0]['layer3'][0]['citations'][0]['quote']='invented';self.save()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(report_cli(args+['--verify-traces','--out-dir',str(self.root/'rejected')]),2)
        self.assertFalse((self.root/'rejected').exists())


if __name__=='__main__':unittest.main()
