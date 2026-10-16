"""S1 experimental per-requirement projection. Never promotes legacy memory counts.

Source contradictions reject the whole task. Local absence/truncation blocks only
dependent checks. Complete memory controls are deliberately not implemented yet.
"""
import hashlib

from blind_annotation import BlindError, pick, sha_file
from .fact_judge import strict_json

POLICY = 'scoped-evidence-v1-draft'
POLICY_TIERED = 'scoped-evidence-v2-tiered'

# Fields the collector used to write as placeholders (sha256 of empty bytes + 0 counts)
# before the fingerprint pass filled them in. Content fields are never in this set.
PRE_FINGERPRINT = ('content_sha256', 'full_bytes', 'stored_bytes')
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()

# Memory evidence levels. None of these is independent-instance verification: they say
# how strongly the snapshot is bound to the run, not that the store was verified out-of-band.
TIER_ABSENT = 'T0_absent_or_error'
TIER_DECLARED = 'T1_declared_snapshot'
TIER_BOUND = 'T2_bound_snapshot'
TIER_CONTROLLED = 'T3_controlled_capture'
TIERS_USABLE = (TIER_DECLARED, TIER_BOUND, TIER_CONTROLLED)


def pre_fingerprint_raw(raw, payload):
    """True when raw/payload differ ONLY in placeholder fingerprint fields.

    Historical raw records were archived before the fingerprint pass ran, so their
    `content_sha256` is sha256(b'') and their byte counts are 0, while `after_content`
    matches the payload. Accepted as a labelled legacy representation: the raw bytes on
    disk are never rewritten, and the payload must still be self-consistent with its own
    content. Any other difference (including content) stays a rejection.
    """
    if not isinstance(raw, dict) or set(raw) != set(payload):
        return False
    if any(raw.get(k) == payload.get(k) for k in PRE_FINGERPRINT):
        return False
    if raw.get('content_sha256') != EMPTY_SHA256:
        return False
    if raw.get('full_bytes') != 0 or raw.get('stored_bytes') != 0:
        return False
    data = (payload.get('after_content') or '').encode('utf8')
    return (payload.get('content_sha256') == hashlib.sha256(data).hexdigest()
            and payload.get('full_bytes') == len(data)
            and payload.get('stored_bytes') == len(data))


def _objects(path):
    rows = [strict_json(s) for s in path.read_text(encoding='utf8').splitlines() if s.strip()]
    if any(not isinstance(r, dict) for r in rows):
        raise BlindError('raw records must be objects')
    return rows


def _path(idx, row):
    if row.get('raw_ref', {}).get('line') != 1:
        raise BlindError('scoped projector requires whole-source references')
    rel = row.get('raw_ref', {}).get('path')
    if not isinstance(rel, str) or '\\' in rel or not rel.startswith('raw/'):
        raise BlindError('invalid raw reference')
    if any(p in ('', '.', '..') for p in rel.split('/')) or ':' in rel:
        raise BlindError('unsafe raw reference')
    path = (idx.run_dir / rel).resolve()
    if not path.is_relative_to((idx.run_dir / 'raw').resolve()):
        raise BlindError('raw source escapes run')
    return path


def project(sample, idx, task_id, tiered=False):
    from .four_layer import valid_path
    source = idx.task_rows(task_id)
    if not source or len({r['id'] for r in source}) != len(source):
        raise BlindError('missing evidence or duplicate ids')
    if [r['seq'] for r in source] != sorted({r['seq'] for r in source}):
        raise BlindError('nonmonotonic evidence')
    tasks = [t for t in idx.manifest.get('tasks', []) if t.get('task_id') == task_id]
    if len(tasks) != 1 or tasks[0].get('status') != 'ok' or not idx.agent_of(task_id):
        raise BlindError('task execution or identity unverified')
    if idx.manifest.get('attempt_retention') is not None and idx.manifest.get('runtime_identity_unchanged',{}).get(idx.agent_of(task_id)) is not True:
        raise BlindError('runtime configuration drift or verification incomplete')
    script = [dict(pick(s, ('session', 'time')), turns=[{'text': t['text']} for t in s['turns']])
              for s in sample['script']]
    expected = {(s['session'], n): t['text'] for s in script for n, t in enumerate(s['turns'], 1)}
    if not expected or len(expected) != sum(len(s['turns']) for s in script):
        raise BlindError('empty or ambiguous script')
    fixtures = [pick(f, ('path', 'content')) for f in sample.get('fixture', [])]
    if any(not valid_path(f['path']) or not isinstance(f['content'], str) for f in fixtures):
        raise BlindError('invalid fixture')
    if len({f['path'] for f in fixtures}) != len(fixtures):
        raise BlindError('duplicate fixture')
    task = dict(pick(sample, ('dimension', 'time_anchor')), script=script, initial_files=fixtures)
    if sample.get('expect', {}).get('boundary_kind') is not None:
        task['boundary_kind'] = sample['expect']['boundary_kind']
    users, replies, positions = {}, {}, []
    events, mapping, hashes, unavailable = [], {}, {}, []
    states = {f['path']: f['content'] for f in fixtures}
    file_bad, snapshots, receipts, listings = {}, {}, {}, []
    disk_fp: dict[str, tuple] = {}   # path -> (disk_bytes, disk_sha256)：物理字节域（PII 脱敏前）
    artifact_rep = {}   # path -> representation label, recorded only in tiered mode
    full_captures, receipt_capture_refs = {}, {}

    for r in source:
        if r.get('run_id') != idx.manifest['run_id'] or r.get('task_id') != task_id:
            raise BlindError('run/task identity mismatch')
        p, kind = r['payload'], r['type']
        path = _path(idx, r)
        present = path.is_file()
        if present:
            hashes[path.relative_to(idx.run_dir.resolve()).as_posix()] = sha_file(path)
        else:
            unavailable.append(r['id'])
        is_memory = kind == 'memory_read' and r['actor'] == 'env'
        is_listing = kind == 'message' and r['source'] == 'system' and p.get('action') == 'workspace_listing'
        is_full_capture = kind == 'message' and r['source'] == 'system' and p.get('action') == 'memory_documents'
        if not present and not (is_memory or kind == 'file_write' or is_listing or is_full_capture):
            raise BlindError('common raw evidence missing')
        body = None
        if kind == 'error':
            raise BlindError('execution error evidence')
        if kind == 'message' and r['source'] == 'dialogue':
            key = (r.get('session_id'), r.get('turn'))
            bucket = users if r['actor'] == 'user' else replies if r['actor'] == 'agent' else None
            if bucket is None or key in bucket or not isinstance(p.get('text'), str):
                raise BlindError('invalid dialogue')
            value = p['text']
            if r['actor'] == 'user':
                if path.read_text(encoding='utf8') != value.replace('\r\n', '\n'):
                    raise BlindError('user raw/payload mismatch')
                if p.get('injected_prefix'):
                    prefix = p.get('prefix_text')
                    if not isinstance(prefix, str) or not value.startswith(prefix + '\n'):
                        raise BlindError('invalid injected prefix')
                    value = value[len(prefix) + 1:]
            # Agent stdout can be an adapter-specific envelope; existence/hash only.
            bucket[key] = value
            positions.append(r['seq'])
            body = pick(p, ('text', 'injected_prefix', 'prefix_text'))
        elif is_memory:
            phase = p.get('phase')
            if phase in snapshots:
                raise BlindError('duplicate snapshot phase')
            snapshots[phase] = r
            if present and not p.get('error'):
                entries = _objects(path)
                if any(not isinstance(e.get('key'), str) or not isinstance(e.get('value'), str) for e in entries):
                    raise BlindError('invalid memory representation')
                if p.get('entries_count') != len(entries) or p.get('coverage', {}).get('entries_scanned') != len(entries):
                    raise BlindError('memory raw/count mismatch')
                if tiered:
                    # Tiered mode publishes the snapshot records as observations, labelled by
                    # tier. Without them a memory requirement has no content to adjudicate and
                    # every memory check dies as 'no scoped observations' even when the agent
                    # really did write memory. scoped-v1 keeps withholding them (unchanged).
                    cov = p.get('coverage') or {}
                    body = pick(p, ('phase', 'via', 'query', 'entries_count'))
                    body['coverage'] = pick(cov, ('files_scanned', 'entries_scanned', 'bytes_scanned'))
                    body['entries'] = [pick(e, ('key', 'value', 'written_at', 'written_at_source')) for e in entries]
            # Controls/full-representation verification is still not claimed: the tier says how
            # bound this snapshot is to the run, never that the store was verified out of band.
        elif kind == 'file_write':
            name = p.get('path')
            if not valid_path(name):
                raise BlindError('invalid artifact path')
            if not present:
                file_bad[name] = 'artifact_raw_missing'
                continue
            raw_objects = _objects(path)
            if raw_objects != [p]:
                if not (tiered and len(raw_objects) == 1 and pre_fingerprint_raw(raw_objects[0], p)):
                    raise BlindError('artifact raw/payload mismatch')
                artifact_rep[name] = 'legacy_pre_fingerprint_raw'
            if p.get('truncated') is not False:
                file_bad[name] = 'artifact_truncated'
                continue
            if p.get('op') == 'delete':
                states.pop(name, None)
                disk_fp.pop(name, None)
            else:
                content = p.get('after_content')
                if not isinstance(content, str):
                    raise BlindError('artifact content missing')
                data = content.encode('utf8')
                if p.get('full_bytes') != len(data) or p.get('stored_bytes') != len(data) or p.get('content_sha256') != hashlib.sha256(data).hexdigest():
                    raise BlindError('artifact fingerprint mismatch')
                states[name] = content
                disk_fp[name] = (p.get('disk_bytes'), p.get('disk_sha256'))
            file_bad.pop(name, None)
            body = pick(p, ('path', 'op', 'before_content', 'after_content'))
        elif kind == 'message' and r['source'] == 'system':
            if p.get('ok') is False or p.get('status') in ('error', 'env_error', 'skip'):
                raise BlindError('failed operational event')
            if is_listing:
                listings.append((r, _objects(path) if present else None))
            elif is_full_capture:
                if present and _objects(path) != [p]:
                    raise BlindError('full memory raw/payload mismatch')
                phase = p.get('phase')
                if phase not in ('before', 'after') or phase in full_captures:
                    raise BlindError('invalid/duplicate full memory capture')
                if present:
                    from .memory_provenance import validate_capture
                    validate_capture(p.get('capture'))
                full_captures[phase] = r
            elif p.get('action') == 'observation_receipt':
                if _objects(path) != [p]:
                    raise BlindError('receipt raw/payload mismatch')
                receipt = p.get('receipt', {})
                phase = receipt.get('window')
                if phase in receipts:
                    raise BlindError('duplicate observation receipt')
                receipts[phase] = receipt
                receipt_capture_refs[phase] = p.get('full_capture_ref')
            elif p.get('action') == 'time_normalization':
                body = pick(p, ('action', 'session', 'anchor', 'workspace_files'))
                body['memory'] = pick(p.get('memory') or {}, ('supported',))
        elif kind == 'tool_result':
            if p.get('status') != 'ok':
                raise BlindError('failed tool result')
        elif kind not in ('file_read', 'memory_read', 'memory_write', 'tool_call'):
            raise BlindError('unsupported evidence kind')
        if body is not None:
            eid = f'E{len(events) + 1}'
            events.append(dict(pick(r, ('session_id', 'turn', 'type', 'actor')), evidence_key=eid, payload=body))
            mapping[eid] = r['id']
    if users != expected or set(replies) != set(expected):
        raise BlindError('script/evidence mismatch')
    memory = {}
    for phase in ('before', 'after'):
        snap = snapshots.get(phase)
        reason = 'memory_scope_control_unverified'
        if snap is None:
            reason = 'memory_snapshot_missing'
        elif snap['id'] in unavailable:
            reason = 'memory_raw_missing'
        elif snap['payload'].get('error'):
            reason = 'memory_observation_error'
        elif (phase == 'before' and snap['seq'] >= min(positions)) or (phase == 'after' and snap['seq'] <= max(positions)):
            raise BlindError('snapshot time scope conflicts with dialogue')
        rec = receipts.get(phase)
        full = full_captures.get(phase)
        if full:
            if snap is None or full['payload'].get('snapshot_ref') != snap['id'] or full['seq'] <= snap['seq']:
                raise BlindError('full memory capture/snapshot mismatch')
            if (phase == 'before' and full['seq'] >= min(positions)) or (phase == 'after' and full['seq'] <= max(positions)):
                raise BlindError('full memory capture window mismatch')
            if full['id'] not in unavailable:
                documents = full['payload']['capture']['documents']
                if snap['payload'].get('coverage', {}).get('files_scanned') != len(documents):
                    raise BlindError('full memory capture/file count mismatch')
        if rec:
            from .observation_contract import validate_receipt
            # Shape/reference validation is NOT independent instance verification.
            from jsonschema import ValidationError
            try:
                validate_receipt(rec, run_id=idx.manifest['run_id'], task_id=task_id,
                                 instance_id=rec['instance_id'], evidence_ids={r['id'] for r in source})
            except ValidationError as exc:
                raise BlindError('invalid observation receipt schema') from exc
            if snap is None or rec['evidence_refs'] != [snap['id']] or rec['item_count'] != snap['payload'].get('entries_count'):
                raise BlindError('receipt/snapshot mismatch')
            if snap['id'] not in unavailable and not snap['payload'].get('error'):
                reason = 'memory_receipt_' + rec['state'] if rec['state'] not in ('complete_empty', 'complete_nonempty') else 'memory_control_verifier_unimplemented'
            capture_ref = receipt_capture_refs[phase]
            if capture_ref is not None:
                if full is None or capture_ref != full['id'] or full['payload'].get('instance_id') != rec['instance_id'] or full['payload'].get('scope_id') != rec['scope_id']:
                    raise BlindError('receipt/full memory capture binding mismatch')
        full_status = 'absent'
        if full:
            full_status = 'raw_missing' if full['id'] in unavailable else 'validated_representation'
            if full_status == 'raw_missing':
                reason = 'full_memory_raw_missing'
        tier = TIER_ABSENT
        if tiered:
            observed = snap is not None and snap['id'] not in unavailable and not snap['payload'].get('error')
            if observed:
                controlled = (rec is not None and rec.get('state') in ('complete_empty', 'complete_nonempty')
                              and full_status == 'validated_representation')
                tier = TIER_CONTROLLED if controlled else (
                    TIER_BOUND if (rec is not None or full is not None) else TIER_DECLARED)
            memory['memory_' + phase] = dict(
                sufficient=tier in TIERS_USABLE, reason=reason, evidence_tier=tier,
                independent_instance_verified=False, full_capture_status=full_status)
        else:
            memory['memory_' + phase] = dict(sufficient=False, reason=reason, full_capture_status=full_status)
    if set(receipts) - {'before', 'after'}:
        raise BlindError('unsupported receipt window')
    listing_status = 'workspace_listing_missing'
    listed = None
    if len(listings) > 1:
        raise BlindError('duplicate workspace listing')
    if listings and listings[0][1] is not None:
        r, entries = listings[0]
        if r['seq'] <= max(positions) or any(e['seq'] > r['seq'] for e in source if e['type'] == 'file_write'):
            raise BlindError('listing is not final')
        listed = {}
        for entry in entries:
            name = entry.get('path')
            if not valid_path(name) or name in listed:
                raise BlindError('invalid listing path')
            listed[name] = entry
        for name in set(states) | set(listed):
            if name in file_bad:
                continue
            if name in states and name not in listed:
                raise BlindError('listing/state mismatch')
            if name not in states:
                file_bad[name] = 'artifact_content_unavailable'
                continue
            data = states[name].encode('utf8')
            # 物理字节域优先：listing 记的是磁盘字节，若 file_write 携带脱敏前的
            # disk_* 指纹就与之比对；否则回退到脱敏文本域（旧证据兼容）。
            disk_bytes, disk_sha = disk_fp.get(name, (None, None))
            expected_size = disk_bytes if isinstance(disk_bytes, int) else len(data)
            expected_sha = disk_sha if isinstance(disk_sha, str) else hashlib.sha256(data).hexdigest()
            if (('size' in listed[name] and listed[name]['size'] != expected_size)
                    or ('sha256' in listed[name] and listed[name]['sha256'] != expected_sha)):
                raise BlindError('listing fingerprint mismatch')
        eid = f'E{len(events) + 1}'
        events.append(dict(type='message', actor='env', evidence_key=eid,
                           payload={'action': 'workspace_listing', 'files': [{'path': n} for n in listed]}))
        mapping[eid] = r['id']
        listing_status = 'complete_final_listing'
    audit = dict(evidence_map=mapping, raw_sha256=hashes, unavailable_evidence=unavailable,
                 memory=memory, listing_status=listing_status, file_bad=file_bad,
                 policy=POLICY_TIERED if tiered else POLICY)
    if tiered:
        audit['artifact_representations'] = artifact_rep
    return {'task': task, 'events': events}, audit


def dependency(audit, check):
    scope = check.get('scope', 'file_final')
    if scope.startswith('memory_'):
        return audit['memory'][scope]
    if scope == 'agent_reply':
        return dict(sufficient=True, reason='complete_dialogue')
    reason = audit['listing_status']
    if reason == 'complete_final_listing':
        reason = audit['file_bad'].get(check['path'], reason)
    return dict(sufficient=reason == 'complete_final_listing', reason=reason)
