"""Read-only config and opt-in retention tests; all execution uses test doubles."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tools'),str(Path(__file__).parent)]
from engine.runtime_identity import capture,unchanged
from engine.adapters.openclaw import OpenClawAdapter
from engine.adapters.hermes import HermesAdapter
from engine.runner import run_batch,StopFlag
from engine.adapter import AdapterError
from test_multirun import SyntheticAdapter,write,read
from capture_runtime_identity import main as cli
from scoring.scoped_evidence import project
from scoring.index import EvidenceIndex
from blind_annotation import BlindError


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.file=self.root/'openclaw.json'
        write(self.file,dict(model='declared-model',temperature=0.4,apiKey='SECRET_NOT_EXPORTED'))
        self.config=dict(home=str(self.root),health_commands=[],runtime_identity_sources={
            f:dict(path=str(self.file),format='json',pointer='/'+f) for f in ('model','temperature')})
    def adapter(self):return OpenClawAdapter(copy.deepcopy(self.config))
    def test_capture_never_invokes_process(self):
        with patch('subprocess.run',side_effect=AssertionError('no process')):r=capture(self.adapter())
        self.assertEqual((r['model'],r['temperature']),('declared-model',0.4))
        self.assertFalse(r['runtime_identity']['effective_settings_verified'])
        self.assertFalse(r['config']['observation_scope']['complete'])
        self.assertNotIn('SECRET_NOT_EXPORTED',json.dumps(r))
    def test_source_and_code_hashes(self):
        from hashlib import sha256
        r=capture(self.adapter())
        self.assertEqual(r['config_files'][0]['sha256'],sha256(self.file.read_bytes()).hexdigest())
        self.assertTrue(r['config']['adapter_version'].startswith('sha256:'))
        self.assertIn('runner.py',r['runtime_identity']['code_sha256'])
    def test_missing_selector_not_guessed(self):
        self.config.pop('runtime_identity_sources');r=capture(self.adapter())
        self.assertIsNone(r['model']);self.assertIsNone(r['temperature'])
    def test_selector_outside_declared_files_rejected(self):
        extra=self.root/'extra.json';write(extra,dict(model='other'))
        self.config['runtime_identity_sources']['model']['path']=str(extra)
        self.assertIsNone(capture(self.adapter())['model'])
    def test_duplicate_json_key_is_unknown(self):
        self.file.write_text('{"model":"a","model":"b","temperature":0}',encoding='utf8')
        self.assertIsNone(capture(self.adapter())['model'])
    def test_boolean_temperature_unknown(self):
        write(self.file,dict(model='a',temperature=True))
        self.assertIsNone(capture(self.adapter())['temperature'])
    def test_missing_file_unknown(self):
        self.file.unlink();r=capture(self.adapter())
        self.assertIsNone(r['model']);self.assertFalse(r['config_files'][0]['exists'])
    def test_config_drift_detected(self):
        a=self.adapter();r=capture(a);write(self.file,dict(model='new',temperature=0.4))
        self.assertFalse(unchanged(a,r))
    def test_hermes_yaml(self):
        path=self.root/'config.yaml';path.write_text('runtime:\n  model: fixture\n  temperature: 0\n',encoding='utf8')
        config=dict(home=str(self.root),runtime_identity_sources={f:dict(path=str(path),format='yaml',pointer='/runtime/'+f) for f in ('model','temperature')})
        r=capture(HermesAdapter(config));self.assertEqual(r['model'],'fixture')
        self.assertEqual(r['config']['observation_scope']['paths'],['MEMORY.md','USER.md'])
        path.write_text('runtime:\n  model: a\n  model: b\n  temperature: 0\n',encoding='utf8')
        self.assertIsNone(capture(HermesAdapter(config))['model'])
    def test_yaml_object_tag_does_not_execute(self):
        path=self.root/'config.yaml';path.write_text('!!python/object/apply:os.system ["invalid"]',encoding='utf8')
        config=dict(home=str(self.root),runtime_identity_sources={f:dict(path=str(path),format='yaml',pointer='/'+f) for f in ('model','temperature')})
        with patch('os.system',side_effect=AssertionError('execution')):
            self.assertIsNone(capture(HermesAdapter(config))['model'])
    def test_cli_readonly_and_no_overwrite(self):
        config=self.root/'adapter.json';write(config,self.config);out=self.root/'identity.json'
        args=['--agent','openclaw','--config',str(config),'--out',str(out)]
        with patch('subprocess.run',side_effect=AssertionError('no process')),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli(args),0);self.assertEqual(cli(args),2)
        self.assertEqual(read(out)['model'],'declared-model')
    def test_cli_unknown_exit_one(self):
        config=self.root/'adapter.json';write(config,dict(home=str(self.root)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli(['--agent','openclaw','--config',str(config),'--out',str(self.root/'out.json')]),1)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.sample=dict(id='sample',dimension='task_reuse',script=[dict(session='s1',turns=[dict(text='First'),dict(text='Second')])])
        self.adapter=SyntheticAdapter('a');self.stop=StopFlag()
    def execute(self,**kw):
        options=dict(adapters=[self.adapter],samples=[self.sample],suite_version='fixture',run_id='retained',
                     runs_dir=self.root,task_retries=0,min_free_mb=0,retained_run=True,stop=self.stop)
        options.update(kw)
        return run_batch(**options)
    def test_existing_directory_rejected_without_write(self):
        rd=self.root/'retained';rd.mkdir();(rd/'keep').write_text('unchanged')
        with self.assertRaises(RuntimeError):self.execute()
        self.assertEqual(list(p.name for p in rd.iterdir()),['keep'])
    def test_retry_option_rejected_before_creation(self):
        with self.assertRaises(RuntimeError):self.execute(task_retries=1)
        self.assertFalse((self.root/'retained').exists())
    def test_resume_option_rejected(self):
        with self.assertRaises(RuntimeError):self.execute(resume=True)
        self.assertFalse((self.root/'retained').exists())
    def test_failure_not_automatically_retried(self):
        calls=[]
        def fail(*a,**k):calls.append(1);raise AdapterError('fixture failed')
        self.adapter.send=fail
        rd,tasks=self.execute();self.assertEqual(len(calls),1)
        self.assertEqual(tasks[0]['attempts'],1);self.assertEqual(tasks[0]['status'],'agent_error')
        self.assertTrue(list((rd/'raw/a-sample').iterdir()))
    def test_interrupted_reply_and_usage_retained(self):
        original=self.adapter.send
        def send(*a,**k):
            r=original(*a,**k);r.usage={'input':11,'output':2};self.stop.request('test-stop');return r
        self.adapter.send=send
        rd,tasks=self.execute()
        self.assertEqual(tasks[0]['status'],'env_error');self.assertTrue(tasks[0]['interrupted'])
        self.assertEqual(tasks[0]['usage'],{'input':11,'output':2})
        rows=[json.loads(s) for s in (rd/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertTrue(any(r['actor']=='agent' and r['payload'].get('text')=='done' for r in rows))
        summary=next(r['payload'] for r in rows if r['payload'].get('action')=='task_summary')
        self.assertEqual(summary['usage'],tasks[0]['usage'])
        self.assertFalse(read(rd/'manifest.json')['usage_complete'])
        with self.assertRaises(BlindError):project(self.sample,EvidenceIndex.load(rd),'a-sample')
    def test_second_attempt_new_directory_preserves_first(self):
        rd,_=self.execute()
        before={p.relative_to(rd):p.read_bytes() for p in rd.rglob('*') if p.is_file()}
        other,_=self.execute(run_id='retry-new')
        self.assertNotEqual(rd,other)
        self.assertTrue(all((rd/p).read_bytes()==data for p,data in before.items()))
    def test_runtime_config_drift_blocks_scoped_scoring(self):
        original=self.adapter.send
        def send(*a,**k):self.adapter.config['changed']='yes';return original(*a,**k)
        self.adapter.send=send
        rd,_=self.execute();m=read(rd/'manifest.json')
        self.assertFalse(m['runtime_identity_unchanged']['a'])
        with self.assertRaisesRegex(BlindError,'configuration drift'):project(self.sample,EvidenceIndex.load(rd),'a-sample')
    def test_default_mode_unchanged(self):
        rd,_=self.execute(retained_run=False)
        m=read(rd/'manifest.json');self.assertNotIn('attempt_retention',m)
        self.assertNotIn('runtime_identity',m['agents'][0])
    def test_unknown_capture_cannot_enter_multirun(self):
        from scoring.multirun import identity
        rd,_=self.execute()
        with self.assertRaisesRegex(ValueError,'unresolved'):identity(read(rd/'manifest.json')['agents'][0])
    def test_real_adapter_readonly_config_to_retained_manifest(self):
        path=self.root/'openclaw.json';write(path,dict(model='synthetic-no-model',temperature=0))
        config=dict(home=str(self.root),workspace=str(self.root/'workspace'),health_commands=[],
            runtime_identity_sources={f:dict(path=str(path),format='json',pointer='/'+f) for f in ('model','temperature')})
        actual=OpenClawAdapter(config)
        # Exercise actual configuration and manifest code; replace every operational action.
        actual.health=lambda:dict(ok=True,checks=[])
        actual.reset=lambda ctx:[]
        actual.watch_health=lambda:[]
        actual.send=self.adapter.send
        actual.probe_memory=self.adapter.probe_memory
        actual.file_root=self.adapter.file_root
        self.adapter=actual
        with patch('subprocess.run',side_effect=AssertionError('no process')):
            rd,tasks=self.execute()
        self.assertEqual(tasks[0]['status'],'ok')
        from scoring.multirun import identity
        m=read(rd/'manifest.json');ident=identity(m['agents'][0])
        self.assertEqual(ident['model'],'synthetic-no-model')
        self.assertTrue(m['runtime_identity_unchanged']['openclaw'])
        self.assertFalse(ident['observation_scope']['instance_independently_verified'])


if __name__=='__main__':unittest.main()
