"""S1 real-evidence binding preflight. Offline checks never open a memory gate."""
import hashlib
import math

from .fact_judge import canonical, strict_json
from .memory_provenance import _hash, _time, validate_capture, verify_control_trace

VERSION = 'okb-memory-admission-v1-draft'
RECEIPT_VERSION = 'okb-memory-admission-receipt-v1-draft'
ROLES = ('instance_binding', 'effective_config', 'storage_inventory', 'control_trace',
         'memory_before', 'memory_after', 'cleanup_receipt', 'deployment_record', 'runtime_identity')
CONTROL_KEYS = ('run_id', 'task_id', 'instance_id', 'scope_id', 'collector_version', 'paths')


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('invalid memory admission fields')


def names(value):
    if (not isinstance(value, list) or not value or
            any(not isinstance(item, str) or not item.strip() for item in value) or
            len(set(value)) != len(value)):
        raise ValueError('nonempty unique names required')
    return value


def review_admission(plan, artifacts):
    exact(plan, ('format', 'binding', 'collector_actor_id', 'effective_parameters', 'window', 'artifact_refs'))
    if plan['format'] != VERSION or not isinstance(plan['collector_actor_id'], str) or not plan['collector_actor_id'].strip():
        raise ValueError('unsupported admission plan or missing collector identity')
    binding = plan['binding']
    exact(binding, (*CONTROL_KEYS, 'profile_id', 'runtime_identity_sha256', 'storage_domains'))
    for field in ('run_id', 'task_id', 'instance_id', 'scope_id', 'collector_version', 'profile_id'):
        if not isinstance(binding[field], str) or not binding[field].strip():
            raise ValueError('missing planned identity')
    names(binding['paths']); names(binding['storage_domains'])
    if not _hash(binding['runtime_identity_sha256']):
        raise ValueError('runtime identity must be locked before evidence review')
    parameters = plan['effective_parameters']
    exact(parameters, ('model', 'temperature', 'top_p'))
    if not isinstance(parameters['model'], str) or not parameters['model'].strip():
        raise ValueError('effective model required')
    for field in ('temperature', 'top_p'):
        if type(parameters[field]) not in (float, int) or not math.isfinite(parameters[field]):
            raise ValueError('effective sampling parameters required')
    exact(plan['window'], ('control_started_at', 'task_started_at', 'task_completed_at', 'capture_completed_at'))
    start, task_start, task_end, end = [_time(plan['window'][field]) for field in
        ('control_started_at', 'task_started_at', 'task_completed_at', 'capture_completed_at')]
    if not start < task_start <= task_end < end:
        raise ValueError('invalid admission window')
    exact(plan['artifact_refs'], ROLES)
    if not isinstance(artifacts, dict) or set(artifacts) - set(ROLES):
        raise ValueError('unknown admission artifacts')
    loaded = {}; missing = []
    for role in ROLES:
        digest = plan['artifact_refs'][role]
        if digest is not None and not _hash(digest):
            raise ValueError('invalid artifact fingerprint')
        if role not in artifacts:
            missing.append(role)
            continue
        raw = artifacts[role]
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('artifact missing locked fingerprint or raw bytes mismatch')
        value = strict_json(raw.decode('utf8'))
        if role not in ('control_trace', 'deployment_record', 'runtime_identity'):
            exact(value, ('format', 'role', 'binding', 'observer_id', 'observed_at', 'details'))
            if value['format'] != RECEIPT_VERSION or value['role'] != role or value['binding'] != binding:
                raise ValueError('admission receipt identity or role mismatch')
            observer = value['observer_id']
            if not isinstance(observer, str) or not observer.strip():
                raise ValueError('receipt observer missing')
            if role in ('instance_binding', 'effective_config', 'storage_inventory', 'cleanup_receipt') and observer == plan['collector_actor_id']:
                raise ValueError('collector declaration is not an independent review')
            if not start <= _time(value['observed_at']) <= end:
                raise ValueError('receipt outside locked window')
        loaded[role] = value
    if missing:
        return dict(format=VERSION, status='evidence_missing', missing=missing,
                    offline_checks_passed=False, memory_gate_eligible=False)
    exact(loaded['instance_binding']['details'], ('deployment_record_sha256',))
    if (loaded['instance_binding']['details']['deployment_record_sha256'] != plan['artifact_refs']['deployment_record'] or
            binding['runtime_identity_sha256'] != plan['artifact_refs']['runtime_identity'] or
            not isinstance(loaded['deployment_record'], dict) or not isinstance(loaded['runtime_identity'], dict)):
        raise ValueError('deployment or runtime identity evidence binding mismatch')
    exact(loaded['effective_config']['details'], ('effective_parameters', 'returned_model'))
    effective = loaded['effective_config']['details']
    if canonical(effective['effective_parameters']) != canonical(parameters) or effective['returned_model'] != parameters['model']:
        raise ValueError('effective parameters or actual returned model mismatch')
    exact(loaded['storage_inventory']['details'], ('domains',))
    domains = loaded['storage_inventory']['details']['domains']
    if not isinstance(domains, list):
        raise ValueError('storage domains must be a list')
    domain_ids = []; paths = []
    for domain in domains:
        exact(domain, ('id', 'paths', 'representation'))
        domain_ids.append(domain['id']); paths.extend(names(domain['paths']))
        if domain['representation'] != 'full_utf8_text_after_declared_pii_v1':
            raise ValueError('storage representation not supported by the current verifier')
    if set(names(domain_ids)) != set(binding['storage_domains']) or set(names(paths)) != set(binding['paths']):
        raise ValueError('storage domains or paths do not cover the planned scope')
    control = loaded['control_trace']
    verified = verify_control_trace(control, expected_binding={key: binding[key] for key in CONTROL_KEYS})
    if control['write_method'] != 'agent_persistence':
        raise ValueError('direct-file fixture is not Agent persistence evidence')
    times = [_time(stage['captured_at']) for stage in control['stages']]
    before = _time(loaded['memory_before']['observed_at'])
    after = _time(loaded['memory_after']['observed_at'])
    if not start <= times[0] < times[1] < times[2] < before <= task_start <= task_end <= after <= end:
        raise ValueError('control, task and memory capture windows mismatch')
    for role in ('memory_before', 'memory_after'):
        exact(loaded[role]['details'], ('capture',))
        capture = validate_capture(loaded[role]['details']['capture'])
        if not capture['enumeration_complete'] or capture['errors']:
            raise ValueError('task memory capture incomplete')
        for document in capture['documents']:
            name = document['path']
            if not any(name == path or (path.endswith('/*.md') and name.startswith(path[:-4]) and name.count('/') == 1 and name.endswith('.md')) for path in paths):
                raise ValueError('task memory document outside planned scope')
        if any(control['marker'] in document['content'] for document in capture['documents']):
            raise ValueError('control marker contaminates task memory')
    cleanup = loaded['cleanup_receipt']
    exact(cleanup['details'], ('restored_domains', 'control_trace_sha256'))
    if (set(names(cleanup['details']['restored_domains'])) != set(binding['storage_domains']) or
            cleanup['details']['control_trace_sha256'] != plan['artifact_refs']['control_trace'] or
            not times[2] <= _time(cleanup['observed_at']) < before):
        raise ValueError('cleanup domains, control evidence or window mismatch')
    return dict(format=VERSION, status='offline_bindings_validated', missing=[],
        offline_checks_passed=True, memory_gate_eligible=False, control=verified,
        limitations=['Artifact bindings do not authenticate observers or independent deployment evidence.',
            'Declared storage inventory cannot prove no omitted persistence domains.',
            'Real operator verification, budget/execution approval and scorer integration remain required.'])
