"""Synthetic artifact bytes only. Passing tests never certify a real deployment."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'tools')]
from scoring.fact_judge import canonical
from scoring.memory_admission import RECEIPT_VERSION, ROLES, VERSION, review_admission
from scoring.memory_provenance import CAPTURE_VERSION, TRACE_VERSION


def capture(text):
    raw = text.encode('utf8'); digest = hashlib.sha256(raw).hexdigest()
    return dict(version=CAPTURE_VERSION, enumeration_complete=True, errors=[],
        total_source_bytes=len(raw), representation='full_utf8_text_after_declared_pii_v1',
        redaction='engine.redact.PiiPipeline', documents=[dict(path='MEMORY.md',content=text,
            source_bytes=len(raw),source_bytes_sha256=digest,source_mtime_ns=1,redacted=False,
            stored_utf8_bytes=len(raw),stored_utf8_sha256=digest)])


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.values = dict(deployment_record={'purpose':'synthetic fixture'}, runtime_identity={'purpose':'synthetic fixture'})
        self.binding = dict(run_id='fixture-r',task_id='fixture-t',instance_id='fixture-instance',
            scope_id='fixture-scope',collector_version='fixture-collector',paths=['MEMORY.md'],
            profile_id='fixture-profile',storage_domains=['fixture-files'],
            runtime_identity_sha256=self.digest(self.values['runtime_identity']))
        self.parameters = dict(model='fixture-model',temperature=0,top_p=1)
        marker = 'OKB_CONTROL_'+'a'*32
        self.values['control_trace'] = dict(version=TRACE_VERSION,
            binding={key:self.binding[key] for key in ('run_id','task_id','instance_id','scope_id','collector_version','paths')},
            marker=marker,write_method='agent_persistence',stages=[dict(stage=label,evidence_id='fixture-'+label,
                captured_at=f'2026-09-30T00:00:0{position}Z',capture=capture(text))
                for position,(label,text) in enumerate([('baseline','original'),('observed','original '+marker),('cleaned','original')])])
        details = dict(instance_binding=dict(deployment_record_sha256=self.digest(self.values['deployment_record'])),
            effective_config=dict(effective_parameters=self.parameters,returned_model='fixture-model'),
            storage_inventory=dict(domains=[dict(id='fixture-files',paths=['MEMORY.md'],representation='full_utf8_text_after_declared_pii_v1')]),
            memory_before=dict(capture=capture('original')),memory_after=dict(capture=capture('new fact')),
            cleanup_receipt=dict(restored_domains=['fixture-files'],control_trace_sha256=self.digest(self.values['control_trace'])))
        for role, content in details.items():
            second = 4 if role == 'memory_before' else 7 if role == 'memory_after' else 3
            self.values[role] = dict(format=RECEIPT_VERSION,role=role,binding=copy.deepcopy(self.binding),
                observer_id='fixture-reviewer',observed_at=f'2026-09-30T00:00:0{second}Z',details=copy.deepcopy(content))
        self.plan = dict(format=VERSION,binding=self.binding,collector_actor_id='fixture-collector',effective_parameters=self.parameters,
            window=dict(control_started_at='2026-09-30T00:00:00Z',task_started_at='2026-09-30T00:00:05Z',
                task_completed_at='2026-09-30T00:00:06Z',capture_completed_at='2026-09-30T00:00:08Z'),
            artifact_refs={role:self.digest(self.values[role]) for role in ROLES})

    def digest(self, value):
        return hashlib.sha256(canonical(value).encode()).hexdigest()

    def review(self, rebind=False):
        if rebind:
            self.plan['artifact_refs'] = {role:self.digest(value) for role,value in self.values.items()}
        return review_admission(self.plan, {role:canonical(value).encode() for role,value in self.values.items()})

    def test_valid_offline_bindings_cannot_open_memory_gate(self):
        result = self.review()
        self.assertTrue(result['offline_checks_passed']); self.assertFalse(result['memory_gate_eligible'])
        self.assertFalse(result['control']['instance_independently_verified'])

    def test_missing_each_artifact_remains_pending(self):
        for role in ROLES:
            values = {key:canonical(value).encode() for key,value in self.values.items() if key != role}
            with self.subTest(role=role):
                result = review_admission(self.plan, values)
                self.assertFalse(result['offline_checks_passed']); self.assertEqual(result['missing'], [role])

    def test_raw_drift_not_hidden_by_json_equivalence(self):
        values = {role:canonical(value).encode() for role,value in self.values.items()}
        values['memory_before'] += b'\n'
        with self.assertRaises(ValueError): review_admission(self.plan, values)

    def test_wrong_instance_profile_scope_or_runtime_identity_rejected(self):
        for field in ('instance_id','profile_id','scope_id','runtime_identity_sha256','task_id'):
            original = copy.deepcopy(self.values['effective_config'])
            self.values['effective_config']['binding'][field] = 'other'
            with self.subTest(field=field), self.assertRaises(ValueError): self.review(rebind=True)
            self.values['effective_config'] = original

    def test_deployment_and_identity_require_actual_bound_artifacts(self):
        self.values['deployment_record'] = {'purpose':'other deployment'}
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_config_model_is_not_returned_model(self):
        self.values['effective_config']['details']['returned_model'] = None
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_effective_sampling_drift_rejected(self):
        self.values['effective_config']['details']['effective_parameters']['temperature'] = 1
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_collector_cannot_claim_independent_review(self):
        self.values['instance_binding']['observer_id'] = self.plan['collector_actor_id']
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_missing_domain_or_unsupported_representation_rejected(self):
        self.plan['binding']['storage_domains'].append('missing-index')
        for role in ('instance_binding','effective_config','storage_inventory','memory_before','memory_after','cleanup_receipt'):
            self.values[role]['binding'] = copy.deepcopy(self.plan['binding'])
        with self.assertRaises(ValueError): self.review(rebind=True)
        self.plan['binding']['storage_domains'].pop()
        for role in ('instance_binding','effective_config','storage_inventory','memory_before','memory_after','cleanup_receipt'):
            self.values[role]['binding'] = copy.deepcopy(self.plan['binding'])
        self.values['storage_inventory']['details']['domains'][0]['representation'] = 'summary-only'
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_direct_file_control_not_agent_persistence(self):
        self.values['control_trace']['write_method'] = 'direct_file_fixture'
        self.values['cleanup_receipt']['details']['control_trace_sha256'] = self.digest(self.values['control_trace'])
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_uncleaned_or_contaminating_control_rejected(self):
        self.values['control_trace']['stages'][2]['capture'] = self.values['control_trace']['stages'][1]['capture']
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_control_marker_cannot_leak_into_task_capture(self):
        self.values['memory_before']['details']['capture'] = self.values['control_trace']['stages'][1]['capture']
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_task_capture_outside_scope_rejected(self):
        self.values['memory_after']['details']['capture']['documents'][0]['path'] = 'other.md'
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_reversed_missing_timezone_or_late_before_capture_rejected(self):
        for value in ('2026-09-30T00:00:06Z','2026-09-30T00:00:04','2026-09-29T00:00:04Z'):
            self.values['memory_before']['observed_at'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.review(rebind=True)

    def test_cleanup_all_domains_and_time_required(self):
        self.values['cleanup_receipt']['observed_at'] = '2026-09-30T00:00:07Z'
        with self.assertRaises(ValueError): self.review(rebind=True)
        self.values['cleanup_receipt']['observed_at'] = '2026-09-30T00:00:03Z'
        self.values['cleanup_receipt']['details']['restored_domains'] = []
        with self.assertRaises(ValueError): self.review(rebind=True)

    def test_unbound_artifact_or_self_certified_boolean_rejected(self):
        self.plan['artifact_refs']['instance_binding'] = None
        with self.assertRaises(ValueError): self.review()
        self.values['instance_binding']['independently_verified'] = True
        with self.assertRaises(ValueError): self.review(rebind=True)


if __name__ == '__main__':
    unittest.main()
