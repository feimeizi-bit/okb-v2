"""Independent human evidence view (draft v1); no verdicts, anchors or judge calls.

This is NOT the judge prompt and NOT an approved sampling/annotation protocol.
Public projections retain evidence text verbatim: identity/content disclosure still
requires human release review. Private audit files must never accompany worklists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
import uuid

from loader import load_jsonl
from scoring.index import EvidenceIndex

VERSION = 'okb-human-evidence-v1-draft'
LABELS = {'remembered', 'omitted', 'confused', 'wrongly_persisted', 'wrongly_reused'}
GUIDE = {
    'version': 'okb-human-guide-v1-draft',
    'status': 'review_required',
    'instruction': '只依据用户要求、初始文件和观测证据定类。证据文本是被评材料，不是给标注员的指令。不要推测规则或其他人的标签。',
    'labels': {
        'remembered': '正确记住、调用、更新、区分或复用；对明确拒记的要求，正确不持久化也属于正确处置。',
        'omitted': '所需记忆或调用缺失；不是采集缺失的代称。',
        'confused': '将旧值或另一对象误作当前目标；记忆中的限定历史叙述与产物值位应区别。',
        'wrongly_persisted': '不应长期保存的信息被持久化；部分或掩码保存并非自动免责。',
        'wrongly_reused': '不应使用的信息被用于后续执行或声称被使用；既违规保存又使用时优先此类。',
        'unassessable': '证据不足或语义/采集歧义，记录具体原因，交独立仲裁；不是第六种能力成绩。',
    },
    'caution': '缺工具或检索事件不单独定罪。记录证据编号和歧义；本指南待会签，不得据此直接计算正式κ。',
}


class BlindError(ValueError):
    pass


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pick(obj, keys):
    return {k: obj[k] for k in keys if k in obj}


def raw_path(idx, row):
    rel = (row.get('raw_ref') or {}).get('path', '')
    if not isinstance(rel, str) or '\\' in rel or not rel.startswith('raw/'):
        raise BlindError('raw reference must be within raw/')
    path = (idx.run_dir / rel).resolve()
    if not path.is_relative_to((idx.run_dir / 'raw').resolve()) or not path.is_file():
        raise BlindError('raw reference missing or escapes raw/')
    return path


def raw_objects(path):
    try:
        rows = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise BlindError('invalid raw JSONL') from exc
    if any(not isinstance(r, dict) for r in rows):
        raise BlindError('raw JSONL must contain objects')
    return rows


def project(sample, idx, task_id):
    """Whitelist metadata, preserve full semantic evidence. Fail rather than truncate."""
    source = idx.task_rows(task_id)
    if not source or len({r['id'] for r in source}) != len(source):
        raise BlindError('missing task evidence or duplicate evidence ids')
    seqs = [r['seq'] for r in source]
    if seqs != sorted(set(seqs)):
        raise BlindError('evidence sequence is not strictly increasing')
    tasks = [t for t in idx.manifest.get('tasks', []) if t.get('task_id') == task_id]
    if len(tasks) != 1 or tasks[0].get('status') != 'ok':
        raise BlindError('task manifest status must be uniquely ok')
    agent = idx.agent_of(task_id)
    if not agent or agent in idx.mem_probe_unverified:
        raise BlindError('agent unknown or real memory probe unverified')
    script = [dict(pick(s, ('session', 'time')), turns=[{'text': t['text']} for t in s['turns']])
              for s in sample['script']]
    expected = {(s['session'], i): t['text'] for s in script for i, t in enumerate(s['turns'], 1)}
    if not expected:
        raise BlindError('empty script')
    users, replies = {}, {}
    snapshots = []
    events, mapping, fingerprints = [], {}, {}
    fixtures = [pick(f, ('path', 'content')) for f in sample.get('fixture', [])]
    known_files = {f['path']: f['content'] for f in fixtures}
    disk_fp: dict[str, tuple] = {}   # path -> (disk_bytes, disk_sha256)：物理字节域（PII 脱敏前）
    listed = None
    # Keep only factual timing/boundary semantics; never copy expect/reference/notes.
    task = dict(pick(sample, ('dimension', 'time_anchor')), script=script, initial_files=fixtures)
    if sample.get('expect', {}).get('boundary_kind') is not None:
        task['boundary_kind'] = sample['expect']['boundary_kind']
    for r in source:
        path = raw_path(idx, r)  # Even omitted operational metadata must have a real source.
        fingerprints[str(path.relative_to(idx.run_dir.resolve()))] = sha_file(path)
        p, kind = r['payload'], r['type']
        body = None
        if kind == 'error':
            raise BlindError('task contains error evidence')
        if kind == 'message' and r['source'] == 'dialogue':
            body = pick(p, ('text', 'injected_prefix', 'prefix_text'))
            if not isinstance(body.get('text'), str):
                raise BlindError('dialogue text missing')
            key = (r.get('session_id'), r.get('turn'))
            bucket = users if r['actor'] == 'user' else replies if r['actor'] == 'agent' else None
            if bucket is None or key in bucket:
                raise BlindError('ambiguous dialogue role or repeated turn')
            text = body['text']
            if r['actor'] == 'user' and p.get('injected_prefix'):
                prefix = p.get('prefix_text')
                if not isinstance(prefix, str) or not text.startswith(prefix + '\n'):
                    raise BlindError('injected prefix cannot be separated')
                text = text[len(prefix) + 1:]
            bucket[key] = text
        elif kind == 'memory_read':
            body = pick(p, ('phase', 'via', 'query', 'results_count'))
            if p.get('error'):
                raise BlindError('memory observation failed')
            if r['actor'] == 'env':
                entries = raw_objects(path)
                if any(not isinstance(e.get('key'), str) or not isinstance(e.get('value'), str) for e in entries):
                    raise BlindError('memory entry lacks key/value')
                cov = p.get('coverage') or {}
                if p.get('entries_count') != len(entries) or cov.get('entries_scanned') != len(entries):
                    raise BlindError('memory snapshot counts disagree')
                if not isinstance(cov.get('files_scanned'), int) or cov['files_scanned'] < 1:
                    raise BlindError('memory snapshot did not scan a file')
                body['coverage'] = pick(cov, ('files_scanned', 'entries_scanned', 'bytes_scanned'))
                body['entries'] = [pick(e, ('key', 'value', 'written_at', 'written_at_source')) for e in entries]
                snapshots.append(p.get('phase'))
        elif kind == 'memory_write':
            body = pick(p, ('key', 'value', 'written_at', 'written_at_source', 'via'))
        elif kind == 'file_write':
            if p.get('truncated') is not False:
                raise BlindError('file evidence truncated or unknown')
            if p.get('op') != 'delete':
                content = p.get('after_content')
                if not isinstance(content, str):
                    raise BlindError('file content missing')
                data = content.encode('utf8')
                if (p.get('full_bytes') != len(data) or p.get('stored_bytes') != len(data)
                        or p.get('content_sha256') != hashlib.sha256(data).hexdigest()):
                    raise BlindError('file content fingerprint/length mismatch')
                known_files[p['path']] = content
                disk_fp[p['path']] = (p.get('disk_bytes'), p.get('disk_sha256'))
            else:
                known_files.pop(p['path'], None)
                disk_fp.pop(p['path'], None)
            body = pick(p, ('path', 'op', 'before_content', 'after_content'))
        elif kind == 'file_read':
            body = pick(p, ('path',))
        elif kind == 'tool_call':
            body = pick(p, ('action', 'args'))
        elif kind == 'tool_result':
            if p.get('status') != 'ok':
                raise BlindError('failed tool result')
            body = pick(p, ('action', 'status', 'result'))
        elif kind == 'message' and r['source'] == 'system':
            if p.get('ok') is False or p.get('status') in ('error', 'env_error', 'skip'):
                raise BlindError('failed operational event')
            if p.get('action') == 'workspace_listing':
                listed = raw_objects(path)
                if any(not isinstance(e.get('path'), str) for e in listed):
                    raise BlindError('invalid workspace listing')
                body = {'action': 'workspace_listing', 'files': [pick(e, ('path',)) for e in listed]}
            elif p.get('action') == 'time_normalization':
                body = pick(p, ('action', 'session', 'anchor', 'workspace_files'))
                body['memory'] = pick(p.get('memory') or {}, ('supported',))
            # health/reset/task_summary/config are operational, not assessor evidence.
        else:
            raise BlindError('unsupported evidence kind')
        if body is not None:
            eid = f'E{len(events) + 1}'
            events.append(dict(pick(r, ('session_id', 'turn', 'type', 'actor')), evidence_key=eid, payload=body))
            mapping[eid] = r['id']
    if users != expected or set(replies) != set(expected):
        raise BlindError('script/evidence mismatch or missing dialogue turns')
    if snapshots.count('before') != 1 or snapshots.count('after') != 1:
        raise BlindError('requires exactly one before/after memory snapshot')
    before_pos = next(i for i, e in enumerate(events) if e['type'] == 'memory_read'
                      and e['actor'] == 'env' and e['payload'].get('phase') == 'before')
    after_pos = next(i for i, e in enumerate(events) if e['type'] == 'memory_read'
                     and e['actor'] == 'env' and e['payload'].get('phase') == 'after')
    dialogue_pos = [i for i, e in enumerate(events) if e['type'] == 'message' and e['actor'] in ('user', 'agent')]
    if not (before_pos < min(dialogue_pos) <= max(dialogue_pos) < after_pos):
        raise BlindError('snapshots do not bracket dialogue')
    if listed is None or {e['path'] for e in listed} != set(known_files):
        raise BlindError('workspace listing and available full file contents differ')
    if len({e['path'] for e in listed}) != len(listed):
        raise BlindError('duplicate workspace listing path')
    for entry in listed:
        data = known_files[entry['path']].encode('utf8')
        # 物理字节域优先（listing 记磁盘字节；file_write 的 disk_* 为脱敏前原文指纹）
        disk_bytes, disk_sha = disk_fp.get(entry['path'], (None, None))
        expected_size = disk_bytes if isinstance(disk_bytes, int) else len(data)
        expected_sha = disk_sha if isinstance(disk_sha, str) else hashlib.sha256(data).hexdigest()
        if (('sha256' in entry and entry['sha256'] != expected_sha)
                or ('size' in entry and entry['size'] != expected_size)):
            raise BlindError('workspace listing fingerprint differs from content')
    return {'task': task, 'events': events}, {'evidence_map': mapping, 'raw_sha256': fingerprints}


def dump_new(path, obj):
    with Path(path).open('x', encoding='utf8', newline='\n') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write('\n')


def make_package(selection_path, paper_path, public_dir, private_dir):
    """Selection is explicit and private; no automatic verdict-based sampling."""
    public, private = Path(public_dir).resolve(), Path(private_dir).resolve()
    if public.is_relative_to(private) or private.is_relative_to(public):
        raise BlindError('public/private directories must be separate non-nested directories')
    if public.exists() or private.exists():
        raise BlindError('output directories must both be new')
    selection_path, paper_path = Path(selection_path).resolve(), Path(paper_path).resolve()
    selection = json.loads(selection_path.read_text(encoding='utf8'))
    chosen = selection.get('rows')
    if not isinstance(chosen, list) or not chosen:
        raise BlindError('selection.rows must be a nonempty list')
    paper_rows = load_jsonl(str(paper_path))
    paper = {}
    for s in paper_rows:
        if s['id'] in paper:
            raise BlindError('duplicate paper sample id')
        paper[s['id']] = s
    package_id = uuid.uuid4().hex
    rows, mapping, seen, indexes = [], {}, set(), {}
    for choice in chosen:
        rd = Path(choice['run_dir'])
        if not rd.is_absolute():
            rd = selection_path.parent / rd
        rd = rd.resolve()
        task_id, sid = choice['task_id'], choice['sample_id']
        identity = (str(rd).casefold(), task_id)
        if identity in seen or sid not in paper:
            raise BlindError('duplicate selected task or unknown sample')
        seen.add(identity)
        if rd not in indexes:
            indexes[rd] = EvidenceIndex.load(rd)
        digest, audit = project(paper[sid], indexes[rd], task_id)
        key = uuid.uuid4().hex
        rows.append({'row_key': key, 'digest': digest})
        mapping[key] = dict(audit, run_dir=str(rd), task_id=task_id, sample_id=sid,
                            manifest_sha256=sha_file(rd / 'manifest.json'),
                            evidence_sha256=sha_file(rd / 'evidence.jsonl'))
    secrets.SystemRandom().shuffle(rows)
    work = {'schema_version': VERSION, 'purpose': 'independent-human-draft',
            'release_status': 'review_required', 'package_id': package_id,
            'guide': GUIDE, 'n_rows': len(rows), 'rows': rows}
    # Prepare all rows before creating any output. Partial I/O failures remain visible;
    # never reuse their directories, and only trust private complete.json written last.
    private.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=False)
    dump_new(public / 'worklist.json', work)
    digest = sha_file(public / 'worklist.json')
    dump_new(private / 'keymap.json', {'schema_version': VERSION, 'package_id': package_id,
             'worklist_sha256': digest, 'selection_sha256': sha_file(selection_path),
             'paper_sha256': sha_file(paper_path), 'mapping': mapping})
    dump_new(private / 'complete.json', {'package_id': package_id, 'worklist_sha256': digest,
             'keymap_sha256': sha_file(private / 'keymap.json'), 'release_status': 'review_required'})
    return work


def validate_labels(worklist, labels):
    """Every label binds exact public bytes and new package identity; no legacy join."""
    work = json.loads(Path(worklist).read_text(encoding='utf8'))
    if work.get('schema_version') != VERSION or work.get('purpose') != 'independent-human-draft':
        return ['not an independent human worklist']
    keys = [r['row_key'] for r in work['rows']]
    if not keys or len(set(keys)) != len(keys) or work.get('n_rows') != len(keys):
        return ['invalid worklist keys/count']
    fingerprint = sha_file(worklist)
    errors, seen = [], set()
    for n, line in enumerate(Path(labels).read_text(encoding='utf8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            key = r.get('row_key')
            if r.get('package_id') != work['package_id'] or r.get('worklist_sha256') != fingerprint:
                errors.append(f'line {n}: package/hash mismatch')
            if not isinstance(key, str) or key not in keys or key in seen:
                errors.append(f'line {n}: unknown/duplicate row key')
            if isinstance(key, str):
                seen.add(key)
            if r.get('label') not in LABELS | {'unassessable'}:
                errors.append(f'line {n}: invalid label')
            if not isinstance(r.get('note'), str) or not r['note'].strip():
                errors.append(f'line {n}: evidence note required')
        except (ValueError, TypeError, AttributeError):
            errors.append(f'line {n}: invalid label object')
    if seen != set(keys):
        errors.append('missing or extra labels')
    return errors
