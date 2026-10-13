"""Full-memory capture, redaction, provenance and cleanup attacks; all offline."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tools'), str(Path(__file__).parent)]
from engine.memory_capture import capture_files, redact_capture, MemoryCaptureError
from engine.redact import PiiPipeline, load_pii
from scoring.memory_provenance import validate_capture, verify_control_trace
from verify_memory_control import main


def stored(root, patterns=None):
    return redact_capture(capture_files(root, patterns or ['MEMORY.md']), PiiPipeline([]), 'e')[0]


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def test_prose_bom_crlf_and_headers_preserved(self):
        value='\ufeff# Past facts\r\nThe OTHER user had an allergy.\r\n\r\nCurrent user has none.\r\n'
        (self.root/'MEMORY.md').write_bytes(value.encode('utf8'))
        capture=stored(self.root);validate_capture(capture)
        self.assertEqual(capture['documents'][0]['content'],value)
        self.assertEqual(capture['documents'][0]['source_bytes'],len(value.encode('utf8')))
    def test_missing_root_differs_from_empty_scope(self):
        missing=stored(self.root/'absent');empty=stored(self.root)
        self.assertFalse(missing['enumeration_complete']);self.assertEqual(missing['errors'],['memory_root_missing'])
        self.assertTrue(empty['enumeration_complete']);self.assertEqual(empty['documents'],[])
        validate_capture(missing);validate_capture(empty)
    def test_zero_bytes_file_not_absent_file(self):
        (self.root/'MEMORY.md').write_bytes(b'')
        c=stored(self.root);validate_capture(c)
        self.assertEqual(len(c['documents']),1);self.assertEqual(c['documents'][0]['content'],'')
    def test_invalid_utf8_not_replaced_or_empty(self):
        (self.root/'MEMORY.md').write_bytes(b'private\xff')
        with self.assertRaises(MemoryCaptureError):capture_files(self.root,['MEMORY.md'])
    def test_byte_limit_has_no_truncated_success(self):
        (self.root/'MEMORY.md').write_bytes(b'12345')
        with self.assertRaises(MemoryCaptureError):capture_files(self.root,['MEMORY.md'],max_file_bytes=4)
        (self.root/'USER.md').write_bytes(b'12345')
        with self.assertRaises(MemoryCaptureError):capture_files(self.root,['MEMORY.md','USER.md'],max_total_bytes=9)
    def test_directory_in_file_slot_rejected(self):
        (self.root/'MEMORY.md').mkdir()
        with self.assertRaises(MemoryCaptureError):capture_files(self.root,['MEMORY.md'])
    def test_pattern_escape_and_overlap_rejected(self):
        (self.root/'MEMORY.md').write_text('x')
        for paths in [['../MEMORY.md'],['/a'],['C:a'],['a\\b'],['**/*.md'],['MEMORY.md','MEMORY.md']]:
            with self.assertRaises(MemoryCaptureError):capture_files(self.root,paths)
    def test_permission_error_not_empty(self):
        with patch('engine.memory_capture._enumerate',side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):capture_files(self.root,['MEMORY.md'])
    def test_enumeration_change_detected(self):
        import engine.memory_capture as M
        (self.root/'MEMORY.md').write_bytes(b'unchanged')
        enumeration=M._enumerate(self.root,['MEMORY.md'])
        with patch.object(M,'_enumerate',side_effect=[enumeration,{}]):
            with self.assertRaises(MemoryCaptureError):capture_files(self.root,['MEMORY.md'])
    def test_reparse_point_rejected(self):
        import types,stat
        fake=types.SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)
        with patch.object(Path,'lstat',return_value=fake):
            with self.assertRaises(MemoryCaptureError):capture_files(self.root,['MEMORY.md'])
    def test_declared_directory_is_not_recursive(self):
        mem=self.root/'memory';mem.mkdir();(mem/'nested').mkdir()
        (mem/'one.md').write_text('one');(mem/'nested/two.md').write_text('two')
        c=stored(self.root,['memory/*.md'])
        self.assertEqual([d['path'] for d in c['documents']],['memory/one.md'])
    def test_pii_redacted_and_hash_domains_separate(self):
        pii=PiiPipeline(load_pii([{'id':'PII_ID_1','kind':'phone','value':'13812345678'}]))
        (self.root/'MEMORY.md').write_text('phone: 13812345678\r\n',encoding='utf8',newline='')
        c,hits=redact_capture(capture_files(self.root,['MEMORY.md']),pii,'ev_test_000001')
        validate_capture(c)
        d=c['documents'][0]
        self.assertIn('<PII_ID_1>',d['content']);self.assertTrue(d['redacted'])
        self.assertNotEqual(d['source_bytes_sha256'],d['stored_utf8_sha256'])
        self.assertNotIn('13812345678',json.dumps([c,hits]))
        self.assertTrue(all(h['evidence_id']=='ev_test_000001' for h in hits))
    def test_stored_or_unredacted_source_tampering_rejected(self):
        (self.root/'MEMORY.md').write_bytes(b'text')
        good=stored(self.root)
        for key,value in [('content','changed'),('source_bytes',0),('source_bytes_sha256','0'*64),('stored_utf8_bytes',0)]:
            bad=copy.deepcopy(good);bad['documents'][0][key]=value
            with self.assertRaises(ValueError):validate_capture(bad)
    def test_complete_enumeration_errors_rejected(self):
        c=stored(self.root);c['errors']=['unreadable']
        with self.assertRaises(ValueError):validate_capture(c)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);f=self.root/'MEMORY.md'
        marker='OKB_CONTROL_'+'a'*32
        f.write_bytes(b'original\r\n');before=stored(self.root)
        f.write_bytes(('original\r\n'+marker+'\r\n').encode());observed=stored(self.root)
        f.write_bytes(b'original\r\n');cleaned=stored(self.root)
        self.binding=dict(run_id='control-r',task_id='control-t',instance_id='configured:i',
                          scope_id='configured_memory_files',collector_version='okb-full-file-scope-v1-draft',paths=['MEMORY.md'])
        self.trace=dict(version='okb-memory-control-trace-v1-draft',binding=copy.deepcopy(self.binding),
                        marker=marker,write_method='direct_file_fixture',stages=[dict(stage=name,evidence_id='e'+str(i),
                        captured_at=f'2026-09-29T00:00:0{i}Z',capture=capture)
                        for i,(name,capture) in enumerate(zip(['baseline','observed','cleaned'],[before,observed,cleaned]))])
    def verify(self):return verify_control_trace(self.trace,expected_binding=self.binding)
    def test_valid_cleanup_still_not_live_identity_proof(self):
        r=self.verify();self.assertTrue(r['baseline_restored'])
        self.assertFalse(r['instance_independently_verified']);self.assertFalse(r['complete_memory_gate_eligible'])
    def test_unrelated_nonempty_memory_not_control_success(self):
        self.trace['stages'][1]['capture']=self.trace['stages'][0]['capture']
        with self.assertRaises(ValueError):self.verify()
    def test_stale_marker_present_in_baseline_rejected(self):
        self.trace['stages'][0]['capture']=self.trace['stages'][1]['capture']
        with self.assertRaises(ValueError):self.verify()
    def test_marker_left_after_cleanup_rejected(self):
        self.trace['stages'][2]['capture']=self.trace['stages'][1]['capture']
        with self.assertRaises(ValueError):self.verify()
    def test_cleanup_removing_other_content_rejected(self):
        (self.root/'MEMORY.md').write_bytes(b'');self.trace['stages'][2]['capture']=stored(self.root)
        with self.assertRaises(ValueError):self.verify()
    def test_cleanup_extra_file_rejected(self):
        (self.root/'extra.md').write_bytes(b'x')
        c=redact_capture(capture_files(self.root,['MEMORY.md','extra.md']),PiiPipeline([]),'e')[0]
        self.binding['paths'].append('extra.md');self.trace['binding']=copy.deepcopy(self.binding)
        self.trace['stages'][2]['capture']=c
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_identity_scope_or_run_rejected(self):
        for key in ('run_id','task_id','instance_id','scope_id','collector_version','paths'):
            t=copy.deepcopy(self.trace);t['binding'][key]=['USER.md'] if key=='paths' else 'other'
            with self.assertRaises(ValueError):verify_control_trace(t,expected_binding=self.binding)
    def test_missing_stage_and_reused_evidence_rejected(self):
        t=copy.deepcopy(self.trace);t['stages'].pop()
        with self.assertRaises(ValueError):verify_control_trace(t,expected_binding=self.binding)
        self.trace['stages'][2]['evidence_id']='e0'
        with self.assertRaises(ValueError):self.verify()
    def test_time_reversal_or_missing_zone_rejected(self):
        for value in ('2026-09-28T00:00:00Z','2026-09-29T00:00:02'):
            self.trace['stages'][2]['captured_at']=value
            with self.assertRaises(ValueError):self.verify()
    def test_partial_enumeration_rejected(self):
        self.trace['stages'][1]['capture']['enumeration_complete']=False
        with self.assertRaises(ValueError):self.verify()
    def test_document_outside_declared_scope_rejected(self):
        self.binding['paths']=['USER.md'];self.trace['binding']=copy.deepcopy(self.binding)
        with self.assertRaises(ValueError):self.verify()
    def test_model_claimed_write_not_a_special_exemption(self):
        self.trace['write_method']='agent_persistence'
        self.assertFalse(self.verify()['complete_memory_gate_eligible'])
    def test_cli_new_output_and_expected_binding(self):
        trace=self.root/'trace.json';binding=self.root/'binding.json';out=self.root/'result.json'
        trace.write_text(json.dumps(self.trace),encoding='utf8');binding.write_text(json.dumps(self.binding),encoding='utf8')
        args=['--trace',str(trace),'--expected-binding',str(binding),'--out',str(out)]
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args),0);saved=out.read_bytes()
            self.assertEqual(main(args),2);self.assertEqual(out.read_bytes(),saved)
            r=json.loads(saved);self.assertFalse(r['complete_memory_gate_eligible'])
            trace.write_text('{"version":"wrong",'+trace.read_text()[1:])
            self.assertEqual(main(args[:-1]+[str(self.root/'bad.json')]),2)
            self.assertFalse((self.root/'bad.json').exists())


class IntegrationTests(unittest.TestCase):
    def test_runner_full_text_pii_and_provenance(self):
        from engine.adapter import AgentAdapter, Capabilities, MemorySnapshot, TurnResult
        from engine.collectors import EvidenceWriter, now_iso
        from engine.observation import file_scope
        from engine.runner import run_task
        from scoring.index import EvidenceIndex
        from scoring.scoped_evidence import project
        class Local(AgentAdapter):
            name='local'
            def capabilities(self):return Capabilities(memory=True,file=True)
            def workspace_ignore(self):return {'MEMORY.md'}
            def reset(self,ctx):
                self.root=ctx.workspace
                (self.root/'MEMORY.md').write_bytes(b'# Context\r\nphone 13812345678; full prose.\r\n')
                return [{'ok':True}]
            def send(self,*args,**kwargs):return TurnResult('ok','ok','',0,0)
            def probe_memory(self,phase, *, for_task_id=None):
                capture=capture_files(self.root,['MEMORY.md'])
                return MemorySnapshot(phase=phase,taken_at=now_iso(),entries=[],
                    coverage=dict(files_scanned=1,entries_scanned=0,bytes_scanned=0,sources=['MEMORY.md']),
                    observation=file_scope(self.root,['MEMORY.md'],'fixture',now_iso(),now_iso(),full_capture=True),
                    full_capture=capture)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rd=root/'run';writer=EvidenceWriter('r',rd)
            sample=dict(id='s',dimension='long_term_retention',script=[dict(session='s1',turns=[{'text':'hello'}])],
                        pii=[{'id':'PII_ID_1','kind':'phone','value':'13812345678'}])
            result=run_task(adapter=Local(),sample=sample,run_id='r',run_dir=rd,writer=writer,work_root=root/'work')
            self.assertEqual(result['status'],'ok',result)
            rows=[json.loads(s) for s in (rd/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
            full=[r for r in rows if r['payload'].get('action')=='memory_documents']
            self.assertEqual(len(full),2)
            for r in full:
                c=r['payload']['capture'];validate_capture(c)
                self.assertIn('# Context\r\nphone <PII_ID_1>',c['documents'][0]['content'])
                self.assertTrue(r['pii_hits'])
                self.assertEqual(json.loads((rd/r['raw_ref']['path']).read_text(encoding='utf8')),r['payload'])
            for p in rd.rglob('*'):
                if p.is_file():self.assertNotIn(b'13812345678',p.read_bytes(),str(p))
            idx=EvidenceIndex(rd,dict(run_id='r',agents=[{'name':'local'}],tasks=[{'task_id':'local-s','status':'ok'}]),rows)
            idx.validate_against_schema()
            _,audit=project(sample,idx,'local-s')
            gate=audit['memory']['memory_after']
            self.assertEqual(gate['full_capture_status'],'validated_representation');self.assertFalse(gate['sufficient'])
            (rd/full[1]['raw_ref']['path']).unlink()
            _,audit=project(sample,idx,'local-s')
            self.assertEqual(audit['memory']['memory_after']['reason'],'full_memory_raw_missing')
            # Bad receipt identity still rejects; local missing does not hide mismatches.
            receipt=[r for r in rows if r['payload'].get('action')=='observation_receipt'][-1]
            receipt['payload']['full_capture_ref']='wrong'
            (rd/receipt['raw_ref']['path']).write_text(json.dumps(receipt['payload'])+'\n')
            with self.assertRaises(ValueError):project(sample,idx,'local-s')
    def test_adapters_keep_full_text_separate_from_parser(self):
        from engine.adapters.openclaw import OpenClawAdapter
        from engine.adapters.hermes import HermesAdapter
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'workspace').mkdir();(root/'memories').mkdir()
            value='\ufeff# Historical context\r\nNot a bullet: current user has no allergy.\r\n'
            for cls,sub in [(OpenClawAdapter,'workspace'),(HermesAdapter,'memories')]:
                (root/sub/'MEMORY.md').write_bytes(value.encode('utf8'))
                adapter=cls({'home':str(root),'workspace':str(root/'workspace')})
                snap=adapter.probe_memory('after')
                self.assertEqual(snap.full_capture['documents'][0]['content'],value)
                self.assertNotIn('full_capture',snap.as_dict())
                self.assertNotIn('parser_does_not_preserve_full_storage_text',snap.observation['limitations'])


if __name__=='__main__':unittest.main()
