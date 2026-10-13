"""S1/S2 offline source attacks and dependency isolation; not model accuracy."""
import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from engine.collectors import EvidenceWriter, now_iso
from engine.observation import file_scope, partial_receipt
from scoring.four_layer import score_task
from scoring.index import EvidenceIndex
from scoring.outcome_v2 import project_row
from scoring.scoped_evidence import project, dependency
from scoring.observation_contract import validate_receipt
from blind_annotation import BlindError


class ScopedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer = EvidenceWriter('run-test', self.root)
        self.sample = dict(id='s', dimension='long_term_retention',
                           script=[dict(session='s1', turns=[{'text':'Remember allergy and write output.'}])],
                           anchors=[{'id':'A1'}, {'id':'A2'}])
        self.rows = []
        self.memory = dict(id='memory', layer='semantic', scope='memory_after',
                           requirement='User has peanut allergy.', basis='s1.t1', on_failure='omitted')
        self.file = dict(id='file', layer='objective', op='file_exists', path='required.json',
                         basis='s1.t1', on_failure='confused')
        self.plan = dict(sample_id='s', checks=[self.memory,self.file], anchor_disposition={
            'A1':dict(role='semantic',checks=['memory'],reason='memory fact'),
            'A2':dict(role='objective',checks=['file'],reason='required output')})
        self.snap('before')
        self.emit('dialogue','message','user',{'text':self.sample['script'][0]['turns'][0]['text']},
                  session_id='s1',turn=1,raw_ref=self.writer.raw.write_text('a-s','request.txt',self.sample['script'][0]['turns'][0]['text']))
        self.emit('dialogue','message','agent',{'text':'Done'},session_id='s1',turn=1,
                  raw_ref=self.writer.raw.write_text('a-s','reply.txt','Done'))
        self.snap('after')
        self.write('other.json','{}')
        listing = [{'path':'other.json','size':2,'sha256':hashlib.sha256(b'{}').hexdigest()}]
        self.emit('system','message','env',{'action':'workspace_listing','files':1},
                  raw_ref=self.writer.raw.write_jsonl('a-s','listing.jsonl',listing))
        self.manifest=dict(run_id='run-test',agents=[{'name':'a','agent_kind':'real'}],tasks=[{'task_id':'a-s','status':'ok'}])

    def emit(self,source,kind,actor,payload,**kwargs):
        r=self.writer.emit(task_id='a-s',source=source,type=kind,actor=actor,payload=payload,**kwargs)
        self.rows.append(r)
        return r

    def snap(self,phase):
        return self.emit('memory','memory_read','env',dict(phase=phase,via='probe',entries_count=0,
            coverage=dict(files_scanned=0,entries_scanned=0,bytes_scanned=0,sources=[])),
            raw_ref=self.writer.raw.write_jsonl('a-s',phase+'.jsonl',[]))

    def write(self,name,content):
        data=content.encode('utf8')
        return self.emit('file','file_write','agent',dict(path=name,op='create',after_content=content,
            before_content=None,full_bytes=len(data),stored_bytes=len(data),truncated=False,
            content_sha256=hashlib.sha256(data).hexdigest()))

    def idx(self):return EvidenceIndex(self.root,self.manifest,self.rows)
    def raw(self,r):return self.root/r['raw_ref']['path']
    def score(self,judge=None):
        row=score_task(self.sample,self.idx(),'a-s',self.plan,plan_sha256='test',judge=judge,evidence_policy='scoped-v1')
        return project_row(row,self.plan['checks'])
    def receipt(self,complete=False):
        snap=self.rows[3]
        meta=file_scope(self.root,['MEMORY.md'],'test',now_iso(),now_iso())
        rec=partial_receipt(meta,run_id='run-test',task_id='a-s',phase='after',evidence_id=snap['id'],item_count=0)
        if complete:
            rec.update(state='complete_empty',enumeration_complete=True,instance_verified=True,
                       control_passed=True,control_cleaned=True,control_refs=[self.rows[1]['id']],errors=[])
        return self.emit('system','message','env',{'action':'observation_receipt','receipt':rec,'scope_metadata':meta})

    def test_missing_memory_does_not_hide_proven_file_failure(self):
        self.raw(self.rows[3]).unlink()
        r=self.score()
        self.assertEqual(r['task_outcome'],'fail');self.assertEqual(r['score'],0)
        self.assertIsNone(r['error_class']);self.assertEqual(r['semantic_requests'],[])
        self.assertEqual(r['layer1']['dependencies']['memory']['reason'],'memory_raw_missing')
        self.assertEqual(r['layer4']['violated_checks'],['file'])
    def test_zero_file_empty_never_counts_as_complete(self):
        r=self.score();self.assertEqual(r['layer3'][0]['result'],'insufficient')
        self.assertEqual(r['layer1']['dependencies']['memory']['reason'],'memory_scope_control_unverified')
    def test_file_pass_does_not_make_whole_task_pass(self):
        self.file['path']='other.json'
        r=self.score();self.assertEqual(r['task_outcome'],'unassessable')
        self.assertEqual(r['layer2'][0]['result'],'supported');self.assertIsNone(r['score'])
    def test_proven_failure_before_memory_has_resolved_class(self):
        self.plan['checks'].reverse()
        r=self.score();self.assertEqual(r['error_class'],'confused')
    def test_receipt_partial_is_visible(self):
        self.receipt();r=self.score()
        self.assertEqual(r['layer1']['dependencies']['memory']['reason'],'memory_receipt_partial')
    def test_forged_complete_flags_do_not_unlock_memory(self):
        self.receipt(complete=True)
        class Never:
            def judge(self,req):raise AssertionError('must not call judge')
        r=self.score(Never())
        self.assertEqual(r['layer1']['dependencies']['memory']['reason'],'memory_control_verifier_unimplemented')
        self.assertEqual(r['semantic_requests'],[])
    def test_receipt_does_not_turn_missing_memory_global(self):
        self.receipt();self.raw(self.rows[3]).unlink()
        self.assertEqual(self.score()['task_outcome'],'fail')
    def test_run_identity_mismatch_blocks_all(self):
        self.rows[3]['run_id']='other'
        self.assertFalse(self.score()['layer1']['valid'])
    def test_raw_escape_blocks_all(self):
        self.rows[3]['raw_ref']['path']='raw/a-s/../../outside'
        self.assertFalse(self.score()['layer1']['valid'])
    def test_wrong_count_blocks_all(self):
        self.rows[3]['payload']['entries_count']=1
        self.assertFalse(self.score()['layer1']['valid'])
    def test_raw_line_mismatch_blocks_all(self):
        self.rows[3]['raw_ref']['line']=2
        self.assertFalse(self.score()['layer1']['valid'])
    def test_duplicate_raw_json_key_blocks_all(self):
        self.raw(self.rows[3]).write_text('{"key":"allergy","value":"yes","value":"no"}\n')
        self.assertFalse(self.score()['layer1']['valid'])
    def test_receipt_identity_mismatch_blocks_all(self):
        r=self.receipt();r['payload']['receipt']['run_id']='wrong'
        self.raw(r).write_text(json.dumps(r['payload'])+'\n')
        self.assertFalse(self.score()['layer1']['valid'])
    def test_duplicate_receipts_block_all(self):
        self.receipt();self.receipt()
        self.assertFalse(self.score()['layer1']['valid'])
    def test_payload_raw_tampering_blocks_all(self):
        self.rows[4]['payload']['after_content']='tampered'
        self.assertFalse(self.score()['layer1']['valid'])
    def test_listing_hash_mismatch_blocks_all(self):
        self.raw(self.rows[5]).write_text(json.dumps({'path':'other.json','sha256':'bad'})+'\n')
        self.assertFalse(self.score()['layer1']['valid'])
    def test_missing_listing_does_not_call_artifact_check(self):
        self.raw(self.rows[5]).unlink()
        r=self.score();self.assertTrue(r['layer1']['valid']);self.assertIsNone(r['score'])
        self.assertEqual(r['layer2'][0]['result'],'insufficient')
    def test_truncated_other_file_does_not_hide_required_absence(self):
        self.rows[4]['payload']['truncated']=True
        self.raw(self.rows[4]).write_text(json.dumps(self.rows[4]['payload'])+'\n')
        self.assertEqual(self.score()['task_outcome'],'fail')
        self.file['path']='other.json'
        self.assertEqual(self.score()['task_outcome'],'unassessable')
    def test_missing_artifact_raw_only_blocks_its_path(self):
        self.raw(self.rows[4]).unlink()
        self.assertEqual(self.score()['task_outcome'],'fail')
        self.file['path']='other.json';self.assertIsNone(self.score()['score'])
    def test_missing_memory_event_is_local(self):
        self.rows.pop(3)
        r=self.score();self.assertEqual(r['task_outcome'],'fail')
        self.assertEqual(r['layer1']['dependencies']['memory']['reason'],'memory_snapshot_missing')
    def test_missing_turn_is_common_failure(self):
        self.rows.pop(2);self.assertFalse(self.score()['layer1']['valid'])
    def test_duplicate_receipt_and_schema_attack(self):
        r=self.receipt();r['payload']['receipt']['state']='invented'
        self.raw(r).write_text(json.dumps(r['payload'])+'\n')
        self.assertFalse(self.score()['layer1']['valid'])
    def test_exact_reply_can_be_judged_without_memory(self):
        c=dict(id='reply',layer='semantic',scope='agent_reply',session='s1',turn=1,
               requirement='Reply confirms completion.',basis='s1.t1',on_failure='omitted')
        self.plan['checks']=[c]
        class Judge:
            model='fixture-only'
            def judge(self,req):
                self.request=req
                return dict(result='supported',rationale='fixture',citations=[dict(
                    evidence_id=req['observations'][0]['evidence_id'],pointer='/text',quote='Done')])
        j=Judge();r=self.score(j)
        self.assertEqual(r['task_outcome'],'pass')
        self.assertEqual(j.request['task_context']['evidence_policy'],'scoped-evidence-v1-draft')
    def test_cli_opt_in_and_frozen_legacy(self):
        from score_four_layer import main
        self.raw(self.rows[3]).unlink()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf8')
        paper=self.root/'paper.jsonl';paper.write_text(json.dumps(self.sample)+'\n',encoding='utf8')
        pp=self.root/'plan.json';pp.write_text(json.dumps(dict(format='okb-four-layer-v1',
            paper_sha256=hashlib.sha256(paper.read_bytes()).hexdigest(),samples=[self.plan])),encoding='utf8')
        with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            args=['--paper',str(paper),'--plan',str(pp),'--run-dir',str(self.root)]
            out=Path(td)/'scoped'
            self.assertEqual(main(args+['--out-dir',str(out),'--evidence-policy','scoped-v1']),2)
            self.assertFalse(out.exists())
            self.assertEqual(main(args+['--out-dir',str(out),'--outcome-version','v2','--evidence-policy','scoped-v1']),0)
            r=json.loads((out/'results.json').read_text())[0]
            self.assertEqual(r['score'],0);self.assertIsNone(r['error_class'])
            old=Path(td)/'old'
            self.assertEqual(main(args+['--out-dir',str(old)]),1)
            self.assertIsNone(json.loads((old/'results.json').read_text())[0]['score'])
            mf=self.root/'manifest.json'
            mf.write_text('{"run_id":"wrong",'+mf.read_text()[1:],encoding='utf8')
            rejected=Path(td)/'duplicate-json'
            self.assertEqual(main(args+['--out-dir',str(rejected),'--outcome-version','v2','--evidence-policy','scoped-v1']),2)
            self.assertFalse(rejected.exists())


class CollectorTests(unittest.TestCase):
    def test_real_adapter_read_only_metadata_empty_and_nonempty(self):
        from engine.adapters.openclaw import OpenClawAdapter
        from engine.adapters.hermes import HermesAdapter
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for cls,sub in [(OpenClawAdapter,'workspace'),(HermesAdapter,'memories')]:
                adapter=cls({'home':str(root),'workspace':str(root/'workspace')})
                target=root/sub;target.mkdir(exist_ok=True)
                snap=adapter.probe_memory('before')
                self.assertEqual(snap.coverage['files_scanned'],0)
                self.assertIn('configured_instance_not_verified',snap.observation['limitations'])
                f=target/'MEMORY.md';f.write_text('- allergy: peanut\n',encoding='utf8')
                before=f.read_bytes();snap=adapter.probe_memory('after')
                self.assertEqual(before,f.read_bytes());self.assertGreater(len(snap.entries),0)
                rec=partial_receipt(snap.observation,run_id='r',task_id='t',phase='after',evidence_id='e',item_count=len(snap.entries))
                validate_receipt(rec,run_id='r',task_id='t',instance_id=rec['instance_id'],evidence_ids=['e'])
                self.assertEqual(rec['state'],'partial');self.assertFalse(rec['instance_verified'])
    def test_runner_emits_receipt_without_changing_memory_schema(self):
        from engine.adapter import AgentAdapter, Capabilities, MemorySnapshot, TurnResult
        from engine.runner import run_task
        class Local(AgentAdapter):
            name='local-fixture'
            def capabilities(self):return Capabilities(memory=True,file=True)
            def reset(self,ctx):
                self.root=ctx.workspace
                return [{'ok':True}]
            def send(self,*args,**kwargs):return TurnResult('ok','ok','',0,0)
            def probe_memory(self,phase, *, for_task_id=None):
                return MemorySnapshot(phase=phase,taken_at=now_iso(),entries=[],
                    coverage=dict(files_scanned=0,entries_scanned=0,bytes_scanned=0,sources=[]),
                    observation=file_scope(self.root,['MEMORY.md'],'fixture',now_iso(),now_iso()))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rd=root/'run';writer=EvidenceWriter('r',rd)
            adapter=Local()
            sample=dict(id='receipt',dimension='long_term_retention',script=[dict(session='s1',turns=[{'text':'hello'}])])
            result=run_task(adapter=adapter,sample=sample,run_id='r',run_dir=rd,writer=writer,work_root=root/'work')
            self.assertEqual(result['status'],'ok',result)
            rows=[json.loads(s) for s in (rd/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
            receipts=[r for r in rows if r['payload'].get('action')=='observation_receipt']
            self.assertEqual(len(receipts),2)
            idx=EvidenceIndex(rd,dict(run_id='r',agents=[{'name':'mock'}]),rows);idx.validate_against_schema()
            for row in receipts:
                rec=row['payload']['receipt']
                self.assertEqual(rec['state'],'partial')
                self.assertEqual(json.loads((rd/row['raw_ref']['path']).read_text(encoding='utf8')),row['payload'])
                self.assertTrue(set(rec['evidence_refs'])<=set(idx.by_id))


if __name__=='__main__':unittest.main()
