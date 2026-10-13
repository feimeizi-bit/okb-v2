"""Offline registered multi-run integration; synthetic adapters, no model calls."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
import testtmp  # 临时目录必须在可写位置：安装态 /var/opt/okb 下普通用户会 PermissionError
from engine.adapter import AgentAdapter, Capabilities, MemorySnapshot, TurnResult
from engine.runner import run_batch
from scoring.fact_judge import SYSTEM, VERSION, fingerprint
from scoring.multirun import PLAN_VERSION, IMPORT_VERSION, identity, load_comparison
from scoring.report_v2 import sha
from score_four_layer import main as score_cli
from report_multirun import main as cli, render_comparison


def write(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def read(path):return json.loads(path.read_text(encoding='utf8'))


class SyntheticAdapter(AgentAdapter):
    agent_kind='mock'
    model_name='synthetic-no-model'
    temperature=0
    def __init__(self,name,produce=True):
        super().__init__(dict(adapter_version='synthetic-v1',executor_version='synthetic-runner-v1',
            observation_scope={'kind':'synthetic-workspace','memory':'none'}))
        self.name=name;self.produce=produce
    def capabilities(self):return Capabilities(dialogue=True,file=True)
    def reset(self,ctx):return []
    def probe_memory(self,phase, *, for_task_id=None):
        return MemorySnapshot(phase,'2026-09-29T00:00:00+00:00',[],dict(files_scanned=0,entries_scanned=0,bytes_scanned=0,sources=[]))
    def send(self,ctx,session,turn_index,text,session_first_turn,timeout=None):
        if self.produce:(ctx.workspace/'out.json').write_text('{"value":1}',encoding='utf8')
        return TurnResult('done','done','',0,1,model=self.model_name)


def declared(adapter):
    return dict(name=adapter.name,agent_kind=adapter.agent_kind,model=adapter.model_name,
        temperature=adapter.temperature,config=adapter.config,config_files=[],capabilities=adapter.capabilities().as_dict())


def fixture(root):
    sample=dict(id='sample',dimension='task_reuse',meta={'suite_version':'v0.2'},score_policy='rule_only',
        expect={'verdict':'remembered'},script=[dict(session='s1',phase='write',turns=[dict(text='Write out.json with value 1.')])],
        anchors=[dict(id='A1',type='file_content',path='out.json',expect='present',severity='hard',on_violation='omitted')])
    paper=root/'paper.jsonl';paper.write_text(json.dumps(sample)+'\n',encoding='utf8')
    check=dict(id='value',layer='objective',op='json_field_equals',path='out.json',field='/value',value=1,
               on_failure='omitted',basis='s1.t1')
    plan=root/'plan.json';write(plan,dict(format='okb-four-layer-v1',paper_sha256=sha(paper.read_bytes()),
        samples=[dict(sample_id='sample',checks=[check],anchor_disposition={'A1':dict(role='objective',checks=['value'],reason='explicit JSON value')})]))
    reg=dict(format=PLAN_VERSION,comparison_id='synthetic-comparison',paper_sha256=sha(paper.read_bytes()),
        plan_sha256=sha(plan.read_bytes()),protocol=dict(outcome_version='okb-four-layer-v2',evidence_policy='scoped-v1',
        mode='prepare',judge_model=None,fact_protocol=VERSION,system_sha256=sha(SYSTEM.encode())),
        agents={a:identity(declared(SyntheticAdapter(a))) for a in ('a','b')},batches=[],attempt_policy='first_attempt_only')
    for rid,rep,retry in [('first',1,False),('second',2,False),('retry',1,True)]:
        reg['batches'].append(dict(run_id=rid,tasks=[dict(task_id=a+'-sample',agent=a,sample_id='sample',repeat=rep,
            attempt_id=rid+'-'+a,retry_of='first-'+a if retry else None) for a in ('a','b')]))
    registration=root/'registration.json';write(registration,reg)
    frozen=sha(registration.read_bytes())
    # Freeze the synthetic registration before running any synthetic task.
    imports=dict(format=IMPORT_VERSION,batches=[])
    for rid,produce in [('first',False),('second',True),('retry',True)]:
        run,_=run_batch(adapters=[SyntheticAdapter(a,produce) for a in ('a','b')],samples=[sample],
            suite_version='v0.2',run_id=rid,runs_dir=root/'runs',task_retries=0,min_free_mb=0,paper_path=paper)
        scored=root/(rid+'-score')
        code=score_cli(['--run-dir',str(run),'--paper',str(paper),'--plan',str(plan),'--out-dir',str(scored),
            '--outcome-version','v2','--evidence-policy','scoped-v1'])
        assert code==0
        imports['batches'].append(dict(run_id=rid,score_dir=str(scored),run_dir=str(run)))
    ip=root/'imports.json';write(ip,imports)
    return registration,frozen,ip,paper,plan


class MultiRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp=testtmp.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):self.args=fixture(self.root)
        self.reg=read(self.args[0]);self.imports=read(self.args[2])
    def save(self):
        write(self.args[0],self.reg);write(self.args[2],self.imports)
        self.args=(self.args[0],sha(self.args[0].read_bytes()),*self.args[2:])
    def report(self):self.save();return load_comparison(*self.args)[0]
    def reject(self):
        self.save()
        with self.assertRaises((ValueError,KeyError,TypeError)):load_comparison(*self.args)
    def cliargs(self):
        return [v for p in zip(('--registration','--registration-sha256','--imports','--paper','--plan'),map(str,self.args)) for v in p]
    def alter_manifest(self,fn):
        entry=self.imports['batches'][0];run=Path(entry['run_dir']);mf=read(run/'manifest.json');fn(mf);write(run/'manifest.json',mf)
        scored=self.root/'changed-score'
        with contextlib.redirect_stdout(io.StringIO()):
            code=score_cli(['--run-dir',str(run),'--paper',str(self.args[3]),'--plan',str(self.args[4]),'--out-dir',str(scored),
                           '--outcome-version','v2','--evidence-policy','scoped-v1'])
        self.assertIn(code,(0,1));entry['score_dir']=str(scored)
    def test_success_and_retry_never_overwrites_failure(self):
        r=self.report();m=r['dimensions']['task_reuse']['agents']['a']
        self.assertEqual((m['planned_tasks'],m['planned_samples'],m['fully_assessable_samples']),(2,1,1))
        self.assertEqual(m['complete_sample_mean'],0.5);self.assertEqual(m['assessed_task_pass_rate'],0.5)
        self.assertEqual(len(r['attempt_history']),6);self.assertEqual(len(r['tasks']),4)
        self.assertEqual([t['task_outcome'] for t in r['attempt_history'] if t['retry_of']],['pass','pass'])
        self.assertEqual([t['task_outcome'] for t in r['tasks'] if t['global_repeat']==1],['fail','fail'])
        self.assertIsNone(r['ranking']);self.assertIsNone(r['overall_score'])
    def test_missing_import_keeps_repeat_denominator(self):
        self.imports['batches']=self.imports['batches'][:1]
        r=self.report();m=r['dimensions']['task_reuse']['agents']['a']
        self.assertEqual(r['missing_batches'],['retry','second'])
        self.assertEqual((m['planned_tasks'],m['fail'],m['unassessable']),(2,1,1))
        self.assertEqual(m['sample_coverage'],0);self.assertIsNone(m['complete_sample_mean'])
        self.assertEqual(r['dimensions']['task_reuse']['common_excluded'][0]['repeat'],'-r2')
    def test_empty_imports_all_planned_visible(self):
        self.imports['batches']=[];r=self.report()
        self.assertEqual(r['imported_attempts'],0);self.assertEqual(len(r['tasks']),4)
        self.assertTrue(all(t['score'] is None for t in r['tasks']))
    def test_retry_without_primary_import_does_not_rescue(self):
        self.imports['batches']=self.imports['batches'][2:];r=self.report()
        self.assertTrue(all(t['score'] is None for t in r['tasks']))
        self.assertEqual(r['imported_attempts'],2)
    def test_duplicate_import_rejected(self):
        self.imports['batches'].append(copy.deepcopy(self.imports['batches'][0]));self.reject()
    def test_run_collision_rejected(self):
        self.imports['batches'][1]['run_dir']=self.imports['batches'][0]['run_dir'];self.reject()
    def test_run_identity_mismatch_rejected(self):
        self.imports['batches'][0]['run_dir'],self.imports['batches'][1]['run_dir']=self.imports['batches'][1]['run_dir'],self.imports['batches'][0]['run_dir']
        self.reject()
    def test_duplicate_primary_rejected(self):
        self.reg['batches'][2]['tasks'][0]['retry_of']=None;self.reject()
    def test_duplicate_attempt_rejected(self):
        self.reg['batches'][1]['tasks'][0]['attempt_id']='first-a';self.reject()
    def test_wrong_retry_unit_rejected(self):
        self.reg['batches'][2]['tasks'][0]['retry_of']='first-b';self.reject()
    def test_retry_branch_rejected(self):
        extra=copy.deepcopy(self.reg['batches'][2]);extra['run_id']='retry-again'
        for t in extra['tasks']:t['attempt_id']='again-'+t['agent']
        self.reg['batches'].append(extra);self.reject()
    def test_unknown_import_rejected(self):
        self.imports['batches'][0]['run_id']='unknown';self.reject()
    def test_unbalanced_plan_rejected(self):
        self.reg['batches'][1]['tasks'].pop();self.reject()
    def test_repeat_gap_rejected(self):
        for t in self.reg['batches'][1]['tasks']:t['repeat']=3
        self.reject()
    def test_boolean_repeat_rejected(self):
        self.reg['batches'][0]['tasks'][0]['repeat']=True;self.reject()
    def test_registration_hash_required(self):
        with self.assertRaises(ValueError):load_comparison(self.args[0],'0'*64,*self.args[2:])
    def test_plan_hash_mismatch_rejected(self):
        self.reg['plan_sha256']='0'*64;self.reject()
    def test_prompt_protocol_mismatch_rejected(self):
        self.reg['protocol']['system_sha256']='0'*64;self.reject()
    def test_mode_mismatch_rejected(self):
        self.reg['protocol'].update(mode='replay',judge_model='fixture');self.reject()
    def test_scope_configuration_mismatch_rejected(self):
        self.reg['agents']['a']['observation_scope']['memory']='other';self.reject()
    def test_missing_runtime_version_rejected(self):
        self.alter_manifest(lambda m:m['agents'][0]['config'].pop('executor_version'));self.reject()
    def test_unknown_runtime_temperature_rejected(self):
        self.alter_manifest(lambda m:m['agents'][0].update(temperature=None));self.reject()
    def test_unknown_runtime_model_rejected(self):
        self.alter_manifest(lambda m:m['agents'][0].update(model=None));self.reject()
    def test_config_fingerprint_mismatch_rejected(self):
        self.alter_manifest(lambda m:m['agents'][0]['config'].update(extra_setting='changed'));self.reject()
    def test_runtime_scope_missing_rejected(self):
        self.alter_manifest(lambda m:m['agents'][0]['config'].pop('observation_scope'));self.reject()
    def test_configuration_file_unavailable_rejected(self):
        self.alter_manifest(lambda m:m['agents'][0].update(config_files=[dict(path='fixture-config',exists=False)]));self.reject()
    def test_duplicate_registered_run_rejected(self):
        self.reg['batches'].append(copy.deepcopy(self.reg['batches'][0]));self.reject()
    def test_score_source_drift_rejected(self):
        path=Path(self.imports['batches'][0]['run_dir'])/'manifest.json'
        path.write_text(path.read_text(encoding='utf8')+' ',encoding='utf8');self.reject()
    def test_duplicate_registration_json_key_rejected(self):
        raw=self.args[0].read_text(encoding='utf8')
        self.args[0].write_text(raw.replace('"format":','"format":"duplicate", "format":',1),encoding='utf8')
        with self.assertRaises(ValueError):
            load_comparison(self.args[0],sha(self.args[0].read_bytes()),*self.args[2:])
    def test_incomplete_score_bundle_rejected(self):
        (Path(self.imports['batches'][0]['score_dir'])/'complete.json').unlink()
        self.save()
        with self.assertRaises(OSError):load_comparison(*self.args)
    def test_observed_model_conflict_rejected(self):
        self.alter_manifest(lambda m:m['tasks'][0].update(models=['other']));self.reject()
    def test_in_run_retry_rejected(self):
        self.alter_manifest(lambda m:m['tasks'][0].update(attempts=2));self.reject()
    def test_resumed_run_rejected(self):
        self.alter_manifest(lambda m:m.update(resumed=True));self.reject()
    def test_extra_batch_task_rejected(self):
        self.reg['batches'][0]['tasks'][0]['task_id']='a-other';self.reject()
    def test_relative_import_paths(self):
        for e in self.imports['batches']:
            for k in ('score_dir','run_dir'):e[k]=str(Path(e[k]).relative_to(self.root))
        self.assertEqual(self.report()['imported_batches'],3)
    def test_html_inert_and_missing_visible(self):
        self.imports['batches']=[];self.reg['comparison_id']='</p><script>bad</script>'
        r=self.report();out=render_comparison(r)
        self.assertNotIn('<script>',out);self.assertIn('&lt;script&gt;',out)
        self.assertIn('缺失批次：first, retry, second',out);self.assertIn('首次计划尝试',out)
    def test_cli_dispatch_and_no_overwrite(self):
        from okb.cli import main
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            args=self.cliargs()+['--out-dir',str(self.root/'report')]
            self.assertEqual(main(['report-multirun']+args),0);self.assertEqual(cli(args),2)
        for name,h in read(self.root/'report/complete.json').items():self.assertEqual(sha((self.root/'report'/name).read_bytes()),h)
    def test_reject_before_creating_output(self):
        self.imports['batches']*=2;self.save()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli(self.cliargs()+['--out-dir',str(self.root/'report')]),2)
        self.assertFalse((self.root/'report').exists())
    def test_output_cannot_change_run_file_set(self):
        out=Path(self.imports['batches'][0]['run_dir'])/'forbidden'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli(self.cliargs()+['--out-dir',str(out)]),2)
        self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
