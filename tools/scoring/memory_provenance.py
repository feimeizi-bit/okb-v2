"""Offline verification of full memory representations and control traces.

No flags in these artifacts establish the live Agent's identity or that a file
scope covers all persistence. Results must not automatically open memory gates.
"""
import hashlib
import re
from datetime import datetime

CAPTURE_VERSION = 'okb-memory-file-capture-v1-draft'
TRACE_VERSION = 'okb-memory-control-trace-v1-draft'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def validate_capture(capture):
    required = {'version', 'documents', 'enumeration_complete', 'errors', 'total_source_bytes', 'representation', 'redaction'}
    if not isinstance(capture, dict) or set(capture) != required:
        raise ValueError('invalid capture fields')
    if capture['version'] != CAPTURE_VERSION or capture['representation'] != 'full_utf8_text_after_declared_pii_v1' or capture['redaction'] != 'engine.redact.PiiPipeline':
        raise ValueError('unsupported capture representation')
    if type(capture['enumeration_complete']) is not bool or not isinstance(capture['errors'], list) or any(not isinstance(x, str) or not x for x in capture['errors']):
        raise ValueError('invalid capture state')
    if capture['enumeration_complete'] and capture['errors']:
        raise ValueError('complete enumeration contains errors')
    if not isinstance(capture['documents'], list):
        raise ValueError('invalid capture documents')
    seen, total = set(), 0
    fields = {'path', 'content', 'source_bytes', 'source_bytes_sha256', 'source_mtime_ns',
              'redacted', 'stored_utf8_bytes', 'stored_utf8_sha256'}
    for doc in capture['documents']:
        if not isinstance(doc, dict) or set(doc) != fields:
            raise ValueError('invalid memory document fields')
        path = doc['path']
        if not isinstance(path, str) or '\\' in path or ':' in path or any(p in ('', '.', '..') for p in path.split('/')) or path in seen:
            raise ValueError('invalid/duplicate memory document path')
        seen.add(path)
        if not isinstance(doc['content'], str) or type(doc['redacted']) is not bool:
            raise ValueError('invalid full memory text')
        for key in ('source_bytes', 'source_mtime_ns', 'stored_utf8_bytes'):
            if type(doc[key]) is not int or doc[key] < 0:
                raise ValueError('invalid source/stored size or timestamp')
        if not _hash(doc['source_bytes_sha256']) or not _hash(doc['stored_utf8_sha256']):
            raise ValueError('invalid source/stored digest')
        data = doc['content'].encode('utf8')
        if len(data) != doc['stored_utf8_bytes'] or _sha(data) != doc['stored_utf8_sha256']:
            raise ValueError('stored full memory fingerprint mismatch')
        if not doc['redacted'] and (len(data) != doc['source_bytes'] or _sha(data) != doc['source_bytes_sha256']):
            raise ValueError('untransformed source fingerprint mismatch')
        total += doc['source_bytes']
    if type(capture['total_source_bytes']) is not int or capture['total_source_bytes'] != total:
        raise ValueError('capture total source bytes mismatch')
    return capture


def _time(value):
    if not isinstance(value, str):
        raise ValueError('invalid control time')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('control time needs timezone')
    return parsed


def verify_control_trace(trace, *, expected_binding):
    """Verify exact marker discovery and cleanup, against caller-locked identity.

    expected_binding must come from the intended deployment/plan, never be derived
    by the caller from the trace itself. Even a match is not a live instance proof.
    This validator has no execution/cleanup/HTTP capability.
    """
    fields = {'version', 'binding', 'marker', 'write_method', 'stages'}
    if not isinstance(trace, dict) or set(trace) != fields or trace['version'] != TRACE_VERSION:
        raise ValueError('invalid control trace')
    keys = {'run_id', 'task_id', 'instance_id', 'scope_id', 'collector_version', 'paths'}
    binding = trace['binding']
    if not isinstance(binding, dict) or set(binding) != keys or binding != expected_binding:
        raise ValueError('control binding mismatch')
    if any(not isinstance(binding[k], str) or not binding[k] for k in keys - {'paths'}):
        raise ValueError('invalid control identity')
    if not isinstance(binding['paths'], list) or not binding['paths'] or any(not isinstance(p, str) for p in binding['paths']) or len(set(binding['paths'])) != len(binding['paths']):
        raise ValueError('invalid control scope paths')
    for p in binding['paths']:
        if not isinstance(p, str) or '\\' in p or ':' in p or any(x in ('', '.', '..') for x in p.split('/')):
            raise ValueError('invalid control scope path')
        if ('*' in p or '?' in p) and not (p.count('/') == 1 and p.endswith('/*.md') and '*' not in p.split('/')[0] and '?' not in p):
            raise ValueError('unsupported control scope pattern')
    marker = trace['marker']
    if not isinstance(marker, str) or not re.fullmatch('OKB_CONTROL_[a-f0-9]{32}', marker):
        raise ValueError('control marker must be a unique random-format token')
    if trace['write_method'] not in ('agent_persistence', 'direct_file_fixture'):
        raise ValueError('unknown control write method')
    stages = trace['stages']
    if not isinstance(stages, list) or len(stages) != 3:
        raise ValueError('control needs baseline, observed and cleaned snapshots')
    ids, times, mappings, hits = [], [], [], []
    for label, stage in zip(('baseline', 'observed', 'cleaned'), stages):
        if not isinstance(stage, dict) or set(stage) != {'stage', 'evidence_id', 'captured_at', 'capture'} or stage['stage'] != label:
            raise ValueError('invalid control stage/order')
        if not isinstance(stage['evidence_id'], str) or not stage['evidence_id']:
            raise ValueError('control evidence id missing')
        ids.append(stage['evidence_id']);times.append(_time(stage['captured_at']))
        capture = validate_capture(stage['capture'])
        if not capture['enumeration_complete'] or capture['errors']:
            raise ValueError('control enumeration incomplete')
        for doc in capture['documents']:
            name = doc['path']
            if not any(name == p or (p.endswith('/*.md') and name.startswith(p[:-4]) and name.count('/') == 1 and name.endswith('.md')) for p in binding['paths']):
                raise ValueError('control document outside declared scope')
        mappings.append({d['path']: (d['source_bytes'], d['source_bytes_sha256']) for d in capture['documents']})
        hits.append(sum(d['content'].count(marker) for d in capture['documents']))
    if len(set(ids)) != 3 or not times[0] < times[1] < times[2]:
        raise ValueError('control evidence reused or time not increasing')
    if hits[0] or hits[2] or hits[1] < 1:
        raise ValueError('control marker discovery/absence not verified')
    if mappings[0] != mappings[2]:
        raise ValueError('control cleanup did not restore baseline bytes/file set')
    return dict(version=TRACE_VERSION, control_sequence_verified=True, marker_observed=True,
                baseline_restored=True, write_method=trace['write_method'], evidence_refs=ids,
                instance_independently_verified=False, complete_memory_gate_eligible=False,
                limitations=['trace integrity is not independent live-instance proof',
                             'declared file scope does not prove all persistent storage coverage',
                             'file bytes restored; API/index/cache side effects are not verified'])
