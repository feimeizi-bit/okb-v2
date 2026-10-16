#!/usr/bin/env python3
"""Read-only fingerprint-domain audit. Matches are explanations, never release waivers.

No content or PII values are emitted. PII materialization is in-memory only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from engine.redact import load_pii, materialize
from engine.workspace import validate_relpath
from scoring.index import EvidenceIndex, InputErrorLocal

VERSION = 'file-evidence-audit-v1'


class AuditError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(text):
    data = text.encode('utf8')
    return {'sha256': sha(data), 'size': len(data)}


def candidates(text, pii):
    """Only exact declared substitutions and standard newline transformations.
    Never brute force secrets or treat equal length alone as evidence.
    """
    bases = [('evidence_text', text)]
    try:
        expanded = materialize(text, load_pii(pii))
    except ValueError:
        expanded = text
    if expanded != text:
        bases.append(('declared_pii_materialized', expanded))
    out = []
    seen = set()
    for domain, value in bases:
        lf = value.replace('\r\n', '\n').replace('\r', '\n')
        for name, value2 in [('as_is', value), ('lf', lf), ('crlf', lf.replace('\n', '\r\n'))]:
            fp = fingerprint(value2)
            identity = (fp['sha256'], fp['size'])
            if identity in seen:
                continue
            seen.add(identity)
            out.append(dict(fp, domain=domain, newline=name))
    return out


def explain(text, listing, pii):
    if (not isinstance(listing.get('sha256'), str) or len(listing['sha256']) != 64
            or any(c not in '0123456789abcdef' for c in listing['sha256'])
            or type(listing.get('size')) is not int or listing['size'] < 0):
        raise AuditError('listing requires valid sha256 and byte size')
    options = candidates(text, pii)
    matched = [o for o in options if o['sha256'] == listing['sha256'] and o['size'] == listing['size']]
    result = 'unresolved'
    if matched:
        m = matched[0]
        if m['domain'] == 'evidence_text':
            result = 'exact' if m['newline'] == 'as_is' else 'newline_representation'
        else:
            result = 'pii_domain' if m['newline'] == 'as_is' else 'pii_and_newline'
    return {'classification': result, 'listing': {k: listing[k] for k in ('sha256', 'size')},
            'reconstructed': fingerprint(text), 'matched_candidates': matched,
            'candidates_tested': len(options)}


def read_raw(idx, row, fingerprints):
    rel = (row.get('raw_ref') or {}).get('path', '')
    if not isinstance(rel, str) or not rel.startswith('raw/') or '\\' in rel:
        raise AuditError('unsafe raw reference')
    path = (idx.run_dir / rel).resolve()
    if not path.is_relative_to((idx.run_dir / 'raw').resolve()) or not path.is_file():
        raise AuditError('missing or escaping raw reference')
    data = path.read_bytes()
    fingerprints[rel] = sha(data)
    rows = [json.loads(line) for line in data.decode('utf8').splitlines() if line.strip()]
    if any(not isinstance(r, dict) for r in rows):
        raise AuditError('raw JSONL must contain objects')
    return rows


def audit_task(sample, idx, task_id):
    source = idx.task_rows(task_id)
    if not source or len({r['id'] for r in source}) != len(source):
        raise AuditError('missing task or duplicate evidence id')
    seqs = [r['seq'] for r in source]
    if seqs != sorted(set(seqs)):
        raise AuditError('non-monotonic evidence sequence')
    files, origin, raw_hashes, writes = {}, {}, {}, []
    for f in sample.get('fixture', []):
        path = validate_relpath(f['path'])
        if path in files or not isinstance(f.get('content'), str):
            raise AuditError('duplicate fixture or invalid fixture content')
        files[path], origin[path] = f['content'], 'fixture'
    listing = None
    for r in source:
        p = r['payload']
        if r['type'] == 'file_write':
            path = validate_relpath(p['path'])
            if p.get('truncated') is not False:
                raise AuditError('truncated file cannot be reconstructed')
            if p.get('op') not in ('create', 'modify', 'delete'):
                raise AuditError('unknown file operation')
            content = p.get('after_content')
            if p['op'] != 'delete' and not isinstance(content, str):
                raise AuditError('file write has no full content')
            data = (content or '').encode('utf8')
            integrity = (p.get('content_sha256') == sha(data) and p.get('full_bytes') == len(data)
                         and p.get('stored_bytes') == len(data))
            raw = read_raw(idx, r, raw_hashes)
            line = (r.get('raw_ref') or {}).get('line')
            if type(line) is not int or line < 1 or line > len(raw):
                raise AuditError('raw line does not resolve')
            raw_payload = raw[line - 1]
            # Compare names only; never emit differing values or content.
            fields = sorted(k for k in set(p) | set(raw_payload) if p.get(k) != raw_payload.get(k))
            writes.append({'evidence_id': r['id'], 'path': path, 'payload_self_consistent': integrity,
                           'raw_differing_fields': fields,
                           'raw_content_equal': raw_payload.get('after_content') == content})
            if p['op'] == 'delete':
                files.pop(path, None)
                origin.pop(path, None)
            else:
                files[path], origin[path] = content, r['id']
        elif r['type'] == 'message' and r.get('source') == 'system' and p.get('action') == 'workspace_listing':
            if listing is not None:
                raise AuditError('multiple workspace listings')
            listing = read_raw(idx, r, raw_hashes)
    if listing is None:
        raise AuditError('missing workspace listing')
    seen, results = set(), []
    for e in listing:
        path = validate_relpath(e['path'])
        if path in seen:
            raise AuditError('duplicate listing path')
        seen.add(path)
        if path not in files:
            raise AuditError('listing file has no reconstructable content')
        results.append(dict(explain(files[path], e, sample.get('pii', [])), path=path, origin=origin[path]))
    if seen != set(files):
        raise AuditError('reconstructed files absent from listing')
    return {'task_id': task_id, 'sample_id': sample['id'], 'files': results, 'writes': writes,
            'raw_sha256': raw_hashes, 'release_authorized': False}


def audit_selection(selection_path, paper_path):
    selection_path, paper_path = Path(selection_path).resolve(), Path(paper_path).resolve()
    selection = json.loads(selection_path.read_text(encoding='utf8'))['rows']
    if not isinstance(selection, list) or not selection:
        raise AuditError('empty selection')
    paper = {}
    for line in paper_path.read_text(encoding='utf8').splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        if s['id'] in paper:
            raise AuditError('duplicate sample id')
        paper[s['id']] = s
    records, indexes, inputs, seen = [], {}, {}, set()
    for item in selection:
        rd = Path(item['run_dir'])
        if not rd.is_absolute():
            rd = selection_path.parent / rd
        rd = rd.resolve()
        key = (str(rd).casefold(), item['task_id'])
        if key in seen:
            raise AuditError('duplicate selected task')
        seen.add(key)
        if rd not in indexes:
            indexes[rd] = EvidenceIndex.load(rd)
            inputs[str(rd)] = {n: sha((rd / n).read_bytes()) for n in ('manifest.json', 'evidence.jsonl')}
        idx = indexes[rd]
        sid, tid = item['sample_id'], item['task_id']
        agent = idx.agent_of(tid)
        stem = agent + '-' + sid
        suffix = tid[len(stem):] if tid.startswith(stem) else None
        if not agent or suffix is None or (suffix and not (suffix.startswith('-r') and suffix[2:].isdigit())):
            raise AuditError('task/sample identity mismatch')
        records.append(dict(audit_task(paper[sid], idx, tid), run_dir=str(rd)))
    return {'version': VERSION, 'scope': 'fingerprint domains only; not semantic or blind release acceptance',
            'paper_sha256': sha(paper_path.read_bytes()), 'selection_sha256': sha(selection_path.read_bytes()),
            'inputs': inputs, 'records': records, 'release_authorized': False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--selection', required=True)
    ap.add_argument('--paper', required=True)
    ap.add_argument('--out', required=True, help='new JSON file, exclusive creation')
    args = ap.parse_args(argv)
    try:
        target = Path(args.out)
        if target.exists():
            raise AuditError('report already exists')
        result = audit_selection(args.selection, args.paper)
        if any(target.resolve().is_relative_to(Path(rd).resolve()) for rd in result['inputs']):
            raise AuditError('report must be outside audited run directories')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf8', newline='\n') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write('\n')
        unresolved = sum(x['classification'] == 'unresolved' for r in result['records'] for x in r['files'])
        inconsistent = sum(not w['payload_self_consistent'] for r in result['records'] for w in r['writes'])
        print(json.dumps({'tasks': len(result['records']), 'unresolved_files': unresolved,
                          'inconsistent_write_payloads': inconsistent, 'release_authorized': False}))
        return int(bool(unresolved or inconsistent))
    except (InputErrorLocal, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'audit failed: {type(exc).__name__}: {exc}; no release authorized', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
