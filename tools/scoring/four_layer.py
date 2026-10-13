"""Opt-in four-layer shadow scorer. Legacy decide/papers/verdicts untouched.
Plans explicitly classify every old anchor, and new requirements are versioned.
No keyword heuristics are promoted into semantic judgments.
"""
from __future__ import annotations
import copy
from blind_annotation import project, BlindError
from .fact_judge import FactError, canonical, fingerprint, request

VERSION = 'okb-four-layer-v1'
FAILURES = {'omitted', 'confused', 'wrongly_persisted', 'wrongly_reused'}
SCOPES = {'memory_before', 'memory_after', 'file_final', 'agent_reply'}
OPS = {'file_exists', 'file_unchanged', 'json_field_equals'}


def valid_path(value):
    return (isinstance(value, str) and bool(value) and '\\' not in value and ':' not in value
            and not value.startswith('/') and all(x not in ('', '.', '..') for x in value.split('/')))


def validate_plan(plan, paper, paper_sha256):
    if not isinstance(plan, dict) or set(plan) != {'format', 'paper_sha256', 'samples'}:
        raise ValueError('invalid plan fields')
    if plan['format'] != VERSION or plan['paper_sha256'] != paper_sha256:
        raise ValueError('plan paper/protocol mismatch')
    if not isinstance(plan['samples'], list) or not plan['samples']:
        raise ValueError('empty plan')
    byid = {s['id']: s for s in paper}
    if len(byid) != len(paper):
        raise ValueError('duplicate paper id')
    seen = set()
    for row in plan['samples']:
        if set(row) != {'sample_id', 'anchor_disposition', 'checks'}:
            raise ValueError('invalid sample plan fields')
        sid = row['sample_id']
        if sid not in byid or sid in seen:
            raise ValueError('unknown or duplicate plan sample')
        seen.add(sid)
        anchors = byid[sid].get('anchors', [])
        ids = [a['id'] for a in anchors]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate source anchor id')
        if not isinstance(row['checks'], list) or not row['checks']:
            raise ValueError('no mandatory checks')
        checks = {}
        for c in row['checks']:
            common = {'id', 'layer', 'on_failure', 'basis'}
            if not isinstance(c, dict) or not common <= set(c):
                raise ValueError('invalid check')
            if not isinstance(c['id'], str) or not c['id'] or c['id'] in checks:
                raise ValueError('duplicate/empty check id')
            if c['on_failure'] not in FAILURES or not isinstance(c['basis'], str) or not c['basis'].strip():
                raise ValueError('failure mapping and user-instruction basis required')
            if c['layer'] == 'objective':
                if c.get('op') not in OPS or not valid_path(c.get('path')):
                    raise ValueError('unsupported objective operation/path')
                extra = {'op', 'path'}
                if c['op'] == 'json_field_equals':
                    extra |= {'field', 'value'}
                    if not isinstance(c.get('field'), str) or not c['field'].startswith('/') or not c['path'].endswith('.json'):
                        raise ValueError('objective field must be an explicit JSON field')
                if set(c) != common | extra:
                    raise ValueError('objective checks cannot contain semantic regexes')
            elif c['layer'] == 'semantic':
                if c.get('scope') not in SCOPES or not isinstance(c.get('requirement'), str) or not c['requirement'].strip():
                    raise ValueError('semantic scope and requirement required')
                extra = {'scope', 'requirement'}
                if c['scope'] == 'file_final':
                    extra.add('path')
                    if not valid_path(c.get('path')):
                        raise ValueError('semantic artifact path invalid')
                if c['scope'] == 'agent_reply':
                    extra |= {'session', 'turn'}
                    if not isinstance(c.get('session'), str) or type(c.get('turn')) is not int or c['turn'] < 1:
                        raise ValueError('reply scope must select an exact turn')
                if set(c) != common | extra:
                    raise ValueError('invalid semantic fields')
            else:
                raise ValueError('unknown layer')
            checks[c['id']] = c
        disp = row['anchor_disposition']
        if not isinstance(disp, dict) or set(disp) != set(ids):
            raise ValueError('must account for every original anchor, no silent dropping')
        used = set()
        for a in disp.values():
            if not isinstance(a, dict) or set(a) != {'role', 'checks', 'reason'} or not isinstance(a['reason'], str) or not a['reason'].strip():
                raise ValueError('anchor role needs explanation')
            if a['role'] not in {'objective', 'semantic', 'mixed', 'diagnostic'} or not isinstance(a['checks'], list):
                raise ValueError('invalid anchor disposition')
            if len(a['checks']) != len(set(a['checks'])) or any(x not in checks for x in a['checks']):
                raise ValueError('bad mapped check ids')
            if a['role'] == 'diagnostic':
                if a['checks']:
                    raise ValueError('diagnostic role has no mandatory check')
            elif not a['checks']:
                raise ValueError('non-diagnostic anchor must map to checks')
            else:
                layers = {checks[x]['layer'] for x in a['checks']}
                if a['role'] != 'mixed' and layers != {a['role']}:
                    raise ValueError('anchor role/check layer mismatch')
            used.update(a['checks'])
        if used != set(checks):
            raise ValueError('new checks must be traceable to anchor dispositions')
    return plan


def _artifacts(digest):
    files = {f['path']: {'exists': True, 'content': f['content'], 'origin': 'fixture'}
             for f in digest['task']['initial_files']}
    for e in digest['events']:
        if e['type'] != 'file_write':
            continue
        p = e['payload']
        files[p['path']] = {'exists': p['op'] != 'delete', 'content': p.get('after_content'),
                            'origin': 'file_write'}
    return files


def observations(digest, check):
    events = digest['events']
    scope = check['scope']
    if scope.startswith('memory_'):
        phase = scope.split('_')[1]
        selected = [e for e in events if e['type'] == 'memory_read' and e['actor'] == 'env'
                    and e['payload'].get('phase') == phase]
        return [{'evidence_id': e['evidence_key'], 'content': copy.deepcopy(e['payload'])} for e in selected]
    if scope == 'agent_reply':
        return [{'evidence_id': e['evidence_key'], 'content': copy.deepcopy(e['payload'])}
                for e in events if e['type'] == 'message' and e['actor'] == 'agent'
                and (e['session_id'], e['turn']) == (check['session'], check['turn'])]
    path = check['path']
    writes = [e for e in events if e['type'] == 'file_write' and e['payload']['path'] == path]
    listing = next(e for e in events if e['payload'].get('action') == 'workspace_listing')
    state = _artifacts(digest).get(path, {'exists': False, 'content': None, 'origin': 'absent'})
    if writes:
        return [{'evidence_id': writes[-1]['evidence_key'], 'content': copy.deepcopy(writes[-1]['payload'])}]
    # Explicit derived observation backed by final listing and initial fixture.
    return [{'evidence_id': listing['evidence_key'], 'content': {'path': path, **state}}]


def objective(digest, c):
    state = _artifacts(digest).get(c['path'], {'exists': False, 'content': None})
    selected = observations(digest, {'scope': 'file_final', 'path': c['path']})
    refs = [x['evidence_id'] for x in selected]
    if c['op'] == 'file_exists':
        ok = state['exists']
    elif c['op'] == 'file_unchanged':
        fixture = {f['path']: f['content'] for f in digest['task']['initial_files']}
        if c['path'] not in fixture:
            raise ValueError('unchanged check requires initial artifact')
        ok = state['exists'] and state['content'] == fixture[c['path']]
    else:
        if not state['exists']:
            ok = False
        else:
            # Strict JSON avoids accepting duplicate keys/nonfinite values.
            from .fact_judge import strict_json
            try:
                value = strict_json(state['content'])
                from .fact_judge import pointer_value
                # JSON pointer, not permissive regex/substring matching.
                value = pointer_value(value, c['field'])
                ok = canonical(value) == canonical(c['value'])
            except (ValueError, TypeError):
                ok = False
    return {'result': 'supported' if ok else 'contradicted', 'evidence_refs': refs,
            'rationale': c['op'] + ': exact artifact/state check'}


def score_task(sample, idx, task_id, plan_row, *, plan_sha256, judge=None, evidence_policy='legacy'):
    if evidence_policy not in ('legacy', 'scoped-v1', 'scoped-v2'):
        raise ValueError('unknown evidence policy')
    tiered = evidence_policy == 'scoped-v2'
    row = {'version': VERSION, 'task_id': task_id, 'sample_id': sample['id'],
           'plan_sha256': plan_sha256, 'status': 'unassessable', 'verdict': None, 'score': None,
           'layer1': {}, 'layer2': [], 'layer3': [], 'layer4': {},
           'evidence_refs': [], 'semantic_requests': []}
    # Conservative existing whole-task evidence gate, not a new exemption list.
    try:
        if evidence_policy in ('scoped-v1', 'scoped-v2'):
            from .scoped_evidence import project as scoped_project
            digest, audit = scoped_project(sample, idx, task_id, tiered=tiered)
        else:
            digest, audit = project(sample, idx, task_id)
    except (BlindError, OSError, ValueError, KeyError, TypeError) as exc:
        row['layer1'] = {'valid': False, 'reason': str(exc)}
        row['layer4'] = {'review_required': True, 'reasons': ['evidence_invalid'],
                         'human_label': None, 'independent_validation': False}
        return row
    row['layer1'] = {'valid': True, 'policy': 'blind-project-conservative-v1',
                     'raw_manifest_sha256': fingerprint(audit['raw_sha256'])}
    checks = []
    if evidence_policy in ('scoped-v1', 'scoped-v2'):
        row['layer1'].update(policy=audit['policy'], dependencies={},
                             unavailable_evidence=audit['unavailable_evidence'])
        if tiered:
            row['layer1']['artifact_representations'] = audit['artifact_representations']
    for c in plan_row['checks']:
        result = {'id': c['id'], 'layer': c['layer'], 'on_failure': c['on_failure']}
        if evidence_policy in ('scoped-v1', 'scoped-v2'):
            from .scoped_evidence import dependency
            gate = dependency(audit, c)
            row['layer1']['dependencies'][c['id']] = gate
            if tiered and gate.get('evidence_tier'):
                result['evidence_tier'] = gate['evidence_tier']
            if not gate['sufficient']:
                result.update(result='insufficient', rationale=gate['reason'], evidence_refs=[])
                row['layer2' if c['layer'] == 'objective' else 'layer3'].append(result)
                checks.append(result)
                continue
        if c['layer'] == 'objective':
            try:
                result.update(objective(digest, c))
            except (ValueError, TypeError, KeyError) as exc:
                result.update(result='insufficient', rationale='objective configuration/evidence error', error=type(exc).__name__, evidence_refs=[])
            row['layer2'].append(result)
        else:
            obs = observations(digest, c)
            # No verdict/score/regex/old outcomes in judge input. All observations included, no keyword slicing.
            context = {'task': digest['task'],
                       'source_binding': fingerprint({'task_id': task_id, 'manifest': idx.manifest,
                                                     'task_rows': idx.task_rows(task_id),
                                                     'raw_sha256': audit['raw_sha256']}),
                       'plan_sha256': plan_sha256,
                       'scope': {k: c[k] for k in ('scope', 'path', 'session', 'turn') if k in c}}
            if evidence_policy in ('scoped-v1', 'scoped-v2'):
                context['evidence_policy'] = audit['policy']
            req = request(c['requirement'], context, obs)
            row['semantic_requests'].append({'check_id': c['id'], 'request_sha256': fingerprint(req), 'request': req})
            try:
                if not obs:
                    raise FactError('no scoped observations')
                if judge is None:
                    raise FactError('semantic judge not supplied')
                from .fact_judge import validate_response
                response = validate_response(req, judge.judge(req))
                result.update(response)
                result['evidence_refs'] = list(dict.fromkeys(x['evidence_id'] for x in response['citations']))
                result['model'] = judge.model
            except Exception as exc:
                result.update(result='insufficient', rationale='semantic verification unavailable or invalid',
                              error=type(exc).__name__, evidence_refs=[])
            row['layer3'].append(result)
        checks.append(result)
    failed = [c for c in checks if c['result'] == 'contradicted']
    unknown = [c for c in checks if c['result'] == 'insufficient']
    reasons = []
    if unknown:
        reasons.append('mandatory_check_unresolved')
    else:
        row['status'] = 'assessed'
        row['verdict'] = failed[0]['on_failure'] if failed else 'remembered'
        row['score'] = 0 if failed else 1
    if failed:
        reasons.append('observed_requirement_failure')
    if tiered:
        # Grading replaces the veto, not the disclosure: a conclusion resting on a merely
        # declared memory snapshot (T1) must stay in the human review queue.
        low = sorted(c['id'] for c in checks if c.get('evidence_tier') == 'T1_declared_snapshot')
        if low:
            reasons.append('low_tier_memory_evidence')
            row['layer4'] = row.get('layer4', {})
            row['low_tier_memory_checks'] = low
    row['evidence_refs'] = list(dict.fromkeys(audit['evidence_map'][e] for c in checks
                for e in c['evidence_refs'] if e in audit['evidence_map']))
    row['layer4'] = {'review_required': bool(reasons), 'reasons': reasons,
                     'violated_checks': [c['id'] for c in failed], 'unresolved_checks': [c['id'] for c in unknown],
                     'priority': 'explicit plan check order; unresolved blocks final classification',
                     'human_label': None, 'independent_validation': False,
                     'sampling': 'random representative audit still required, separate from disputes'}
    return row
