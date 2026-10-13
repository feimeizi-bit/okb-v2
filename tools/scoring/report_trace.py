"""Offline reconstruction of score references; never invokes a judge.

This verifies compatibility with the CURRENT projector, not the identity of the
code that originally scored a bundle, model authorship, or semantic correctness.
"""
from .fact_judge import canonical, validate_response
from .four_layer import score_task, observations
from .index import EvidenceIndex

VERSION = 'okb-report-trace-v1-draft'


def same(actual, expected, label):
    if canonical(actual) != canonical(expected):
        raise ValueError('trace mismatch: ' + label)


def verify_traces(bundle, rows, run_dir):
    idx = EvidenceIndex.load(run_dir, validate_schema=True)
    policy = bundle['summary'].get('evidence_policy', 'legacy')
    tiered = policy == 'scoped-v2'
    if policy in ('scoped-v1', 'scoped-v2'):
        from .scoped_evidence import project as scoped_project

        def project(sample, idx, tid):
            # Re-project with the same tiering the bundle was scored with: scoped-v1 must
            # not be re-projected as tiered (its memory veto would change the trace).
            return scoped_project(sample, idx, tid, tiered=policy == 'scoped-v2')
    else:
        from blind_annotation import project
    targets = {t['task_id']: t for t in bundle['tasks']}
    for row in rows:
        tid, sid = row['task_id'], row['sample_id']
        sample, plan = bundle['samples'][sid], bundle['selected'][sid]
        expected = score_task(sample, idx, tid, plan,
            plan_sha256=bundle['lock']['plan_sha256'], evidence_policy=policy)
        same(row['layer1'], expected['layer1'], 'evidence gate')
        same(row['semantic_requests'], expected['semantic_requests'], 'semantic requests')
        same(row['layer2'], expected['layer2'], 'objective checks')
        if not expected['layer1']['valid']:
            same(row['evidence_refs'], [], 'invalid source references')
            targets[tid]['trace'] = dict(status='source_invalid', checks=[],
                reason=expected['layer1']['reason'])
            continue
        digest, audit = project(sample, idx, tid)
        raw_hashes = {p.replace('\\', '/'): h for p,h in audit['raw_sha256'].items()}
        requests = {q['check_id']: q for q in expected['semantic_requests']}
        pending = {c['id']: c for c in expected['layer3']}
        vector = {c['id']: c for c in row['layer2'] + row['layer3']}
        traces, used = [], []
        for definition in plan['checks']:
            cid = definition['id']
            result = vector[cid]
            req = requests.get(cid)
            if definition['layer'] == 'semantic':
                if req is None:
                    same(result, pending[cid], 'blocked semantic check')
                elif 'citations' in result:
                    expected_keys = {'id', 'layer', 'on_failure', 'result', 'rationale',
                                     'citations', 'evidence_refs', 'model'}
                    seen_keys = set(result)
                    if tiered and 'evidence_tier' in seen_keys:
                        # scoped-v2 stamps a tier only on memory-scoped checks, so the key is
                        # optional here; file/reply-scoped conclusions legitimately carry none.
                        seen_keys.discard('evidence_tier')
                    if seen_keys != expected_keys:
                        raise ValueError('trace mismatch: semantic result shape')
                    validate_response(req['request'], {k: result[k] for k in
                                      ('result', 'rationale', 'citations')})
                    same(result['evidence_refs'], list(dict.fromkeys(
                        c['evidence_id'] for c in result['citations'])), 'citation references')
                    if not result['model'] or result['model'] != bundle['summary'].get('model'):
                        raise ValueError('trace mismatch: judge model')
                else:
                    # The precise transport error cannot be reconstructed offline.
                    candidate = dict(result)
                    if not isinstance(candidate.get('error'), str) or not candidate['error']:
                        raise ValueError('trace mismatch: missing unavailable error')
                    candidate['error'] = pending[cid]['error']
                    same(candidate, pending[cid], 'unavailable semantic check')
            gate = row['layer1'].get('dependencies', {}).get(cid)
            selected = []
            if not gate or gate['sufficient']:
                if req:
                    selected = req['request']['observations']
                elif definition['layer'] == 'objective' and result['evidence_refs']:
                    selected = observations(digest, dict(scope='file_final', path=definition['path']))
            links = []
            for obs in selected:
                eid = obs['evidence_id']
                original_id = audit['evidence_map'][eid]
                source = idx.by_id[original_id]
                if source['task_id'] != tid or source['run_id'] != bundle['run_id']:
                    raise ValueError('trace mismatch: cross-task observation')
                derived = (definition.get('scope', 'file_final') == 'file_final'
                           and source['payload'].get('action') == 'workspace_listing')
                links.append(dict(evidence_id=eid, original_id=original_id,
                    raw_ref=source['raw_ref'], raw_sha256=raw_hashes[source['raw_ref']['path']],
                    representation='derived_file_state' if derived else 'projected_observation',
                    derivation=dict(path=definition['path'], initial_files=digest['task']['initial_files'],
                                    basis='final listing plus initial fixture and ordered file events') if derived else None,
                    content=obs['content'], cited=eid in result['evidence_refs']))
            for eid in result['evidence_refs']:
                if eid not in {o['evidence_id'] for o in selected}:
                    raise ValueError('trace mismatch: reference outside check scope')
                used.append(audit['evidence_map'][eid])
            traces.append(dict(check_id=cid, status='blocked' if gate and not gate['sufficient'] else
                'objective_recomputed' if definition['layer']=='objective' else
                'citations_verified' if 'citations' in result else 'request_verified_judge_unavailable',
                dependency=gate, request_sha256=req['request_sha256'] if req else None,
                citations=result.get('citations', []), observations=links))
        same(row['evidence_refs'], list(dict.fromkeys(used)), 'task evidence union')
        targets[tid]['trace'] = dict(status='verified', checks=traces)
    bundle['trace_verification'] = dict(format=VERSION, status='verified_current_projection',
        tasks=len(rows), source_invalid=sum(not r['layer1']['valid'] for r in rows),
        semantic_truth_verified=False, original_scorer_identity_verified=False)
