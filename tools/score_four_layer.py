#!/usr/bin/env python3
"""Four-layer opt-in shadow scorer. Fresh output outside runs; no default live calls.
Input plan accounts for every original anchor. Does not write legacy verdicts.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from scoring.fact_judge import strict_json, fingerprint, ReplayFacts, LiveFacts
from scoring.four_layer import VERSION, validate_plan, score_task
from scoring.index import EvidenceIndex
from scoring.decide import decide_task


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, obj):
    with Path(path).open('x', encoding='utf8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--paper', required=True)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--out-dir', required=True)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--replay', help='Exact-model/request facts JSONL, never old judge caches')
    mode.add_argument('--live', action='store_true')
    ap.add_argument('--model')
    ap.add_argument('--base-url')
    ap.add_argument('--api-key-env', default='OKB_FACT_JUDGE_API_KEY')
    ap.add_argument('--max-calls', type=int)
    ap.add_argument('--max-output-tokens', type=int)
    ap.add_argument('--budget-file', help='Explicit currency, prices and request reservation JSON; live only')
    ap.add_argument('--runtime-contract-file', help='Cost-bound runtime contract; required for live')
    ap.add_argument('--cost-group-file', help='Path-bound cumulative judge group; required for live')
    ap.add_argument('--cost-scope', help='Exact declared scope identifier; required for live')
    from scoring.cost_review import WIRE_PROFILES
    ap.add_argument('--wire-profile', choices=WIRE_PROFILES, default='legacy-chat-v1')
    ap.add_argument('--reasoning-effort', choices=['low','medium','high','xhigh','max'])
    ap.add_argument('--authorize-paid-calls', action='store_true')
    ap.add_argument('--outcome-version', choices=['v1', 'v2'], default='v1',
                    help='v2 separates proven task failure from unresolved error classification')
    ap.add_argument('--evidence-policy', choices=['legacy', 'scoped-v1', 'scoped-v2'], default='legacy',
                    help='per-check evidence gate; scoped-v1 vetoes unverified memory, '
                         'scoped-v2 grades memory into evidence tiers T0-T3 (T0 stays unassessable); both require v2')
    args = ap.parse_args(argv)
    try:
        if args.evidence_policy != 'legacy' and args.outcome_version != 'v2':
            raise ValueError('scoped evidence requires v2 outcomes')
        paper_path, plan_path, rd, out = [Path(x).resolve() for x in (args.paper,args.plan,args.run_dir,args.out_dir)]
        if out.exists() or not out.parent.is_dir() or out.is_relative_to(rd):
            raise ValueError('output must be new, outside input run, with existing parent')
        sources = [paper_path, plan_path] + ([Path(args.replay).resolve()] if args.replay else [])
        if args.budget_file:
            sources.append(Path(args.budget_file).resolve())
        if args.runtime_contract_file:
            sources.append(Path(args.runtime_contract_file).resolve())
        if args.cost_group_file:
            sources.append(Path(args.cost_group_file).resolve())
        if any(p.is_relative_to(out) for p in sources):
            raise ValueError('output overlaps source inputs')
        if args.authorize_paid_calls and not args.live:
            raise ValueError('paid authorization only applies with --live')
        if not args.live and (args.base_url or args.max_calls is not None or args.budget_file or args.runtime_contract_file or args.cost_group_file or args.cost_scope or args.max_output_tokens is not None):
            raise ValueError('transport options require live mode')
        if not args.live and (args.wire_profile != 'legacy-chat-v1' or args.reasoning_effort is not None):
            raise ValueError('wire profile options require live mode')
        if args.live and not args.authorize_paid_calls:
            raise ValueError('explicit paid authorization required')
        if (args.replay or args.live) and not args.model:
            raise ValueError('explicit model required')
        if args.live:
            from scoring.audited_facts import AuditedFacts, validate_budget
            if not args.budget_file or type(args.max_output_tokens) is not int or not 1 <= args.max_output_tokens <= 32768:
                raise ValueError('live mode needs budget file and output limit')
            budget = validate_budget(strict_json(Path(args.budget_file).read_text(encoding='utf8')))
            if args.model == 'gpt-6.1-sol' and (args.wire_profile != 'openai-cache-chat-v1' or budget.get('format') != 'okb-judge-budget-v2-draft'):
                raise ValueError('current GPT live calls require the four-bucket cache billing profile')
            if not args.runtime_contract_file:
                raise ValueError('live mode needs a cost-bound runtime contract')
            contract = strict_json(Path(args.runtime_contract_file).read_text(encoding='utf8'))
            if not args.cost_group_file or not args.cost_scope:
                raise ValueError('live mode needs a cumulative cost group and exact scope')
            from scoring.cost_group import CostGroup
            cost_group = CostGroup(strict_json(Path(args.cost_group_file).read_text(encoding='utf8')))
            cost_group.validate_scope(args.cost_scope, contract)
            if Path(cost_group.policy['ledger_path']).is_relative_to(rd):
                raise ValueError('mutable cost ledger overlaps input run')
        frozen = {str(p): sha(p) for p in sources}
        for p in rd.rglob('*'):
            if p.is_file():
                frozen[str(p)] = sha(p)
        paper = [strict_json(s) for s in paper_path.read_text(encoding='utf8').splitlines() if s.strip()]
        plan = strict_json(plan_path.read_text(encoding='utf8'))
        validate_plan(plan, paper, frozen[str(paper_path)])
        if args.evidence_policy != 'legacy':
            # The legacy index is permissive JSON. Reject ambiguous source bytes
            # before its load in the opt-in path without changing historical v1.
            strict_json((rd/'manifest.json').read_text(encoding='utf8'))
            for line in (rd/'evidence.jsonl').read_text(encoding='utf8').splitlines():
                if line.strip():
                    strict_json(line)
        idx = EvidenceIndex.load(rd, validate_schema=True)
        ids = [r['id'] for r in idx.rows]
        seq = [r['seq'] for r in idx.rows]
        if len(ids) != len(set(ids)) or seq != sorted(set(seq)):
            raise ValueError('duplicate evidence IDs or nonmonotonic sequence')
        selected = {r['sample_id']: r for r in plan['samples']}
        samples = {s['id']: s for s in paper}
        tasks = []
        for t in idx.manifest.get('tasks', []):
            tid = t['task_id']; ag = idx.agent_of(tid)
            if not ag:
                raise ValueError('unknown manifest task agent')
            suffix = tid[len(ag)+1:]
            import re
            sid = suffix if suffix in samples else re.sub(r'-r\d+$', '', suffix)
            if sid in selected:
                tasks.append((tid, sid))
        if not tasks or len(tasks) != len({t[0] for t in tasks}) or {sid for _,sid in tasks} != set(selected):
            raise ValueError('missing selected sample tasks or duplicate manifest tasks')
        judge = None
        if args.replay:
            records = [strict_json(s) for s in Path(args.replay).read_text(encoding='utf8').splitlines() if s.strip()]
            judge = ReplayFacts(records, args.model)
        elif args.live:
            from scoring.cost_runtime import validate_runtime_contract
            reviewed_requests = []
            for tid, sid in tasks:
                prepared = score_task(samples[sid], idx, tid, selected[sid], plan_sha256=sha(plan_path),
                                      judge=None, evidence_policy=args.evidence_policy)
                reviewed_requests.extend(dict(row, source=dict(kind='historical_prepare',
                    task_id=tid, check_id=row['check_id'])) for row in prepared['semantic_requests'])
            validate_runtime_contract(contract, reviewed_requests, model=args.model, base_url=args.base_url,
                max_output_tokens=args.max_output_tokens, budget=budget, max_calls=args.max_calls,
                wire_profile=args.wire_profile, reasoning_effort=args.reasoning_effort)
            # Validate transport before creating output; no request is sent here.
            judge = LiveFacts(base_url=args.base_url or '', api_key=os.environ.get(args.api_key_env, ''),
                              model=args.model, max_calls=args.max_calls)
        out.mkdir()
        if args.live:
            judge = AuditedFacts(base_url=args.base_url, api_key=os.environ.get(args.api_key_env,''),
                                 model=args.model, max_calls=args.max_calls,
                                 max_output_tokens=args.max_output_tokens, budget=budget,
                                 journal_path=out/'call-journal.jsonl',runtime_contract=contract,
                                 reviewed_requests=reviewed_requests, wire_profile=args.wire_profile,
                                 reasoning_effort=args.reasoning_effort,
                                 cost_group=cost_group, cost_scope=args.cost_scope)
        result_version = VERSION if args.outcome_version == 'v1' else 'okb-four-layer-v2'
        lock = {'format':result_version,'sha256':frozen,'plan_sha256':sha(plan_path)}
        if args.evidence_policy != 'legacy':
            lock['evidence_policy'] = args.evidence_policy
        write_new(out/'input-lock.json', lock)
        results = []
        for tid, sid in tasks:
            row = score_task(samples[sid], idx, tid, selected[sid], plan_sha256=sha(plan_path),
                             judge=judge, evidence_policy=args.evidence_policy)
            if args.outcome_version == 'v2':
                from scoring.outcome_v2 import project_row
                row = project_row(row, selected[sid]['checks'])
            # Separate historical rule baseline, never fed to semantic judge.
            try:
                if not row['layer1']['valid']:
                    raise ValueError('legacy baseline suppressed for invalid evidence')
                baseline = decide_task(samples[sid], idx, tid)
                row['legacy_rule_baseline'] = {k: getattr(baseline,k) for k in ('status','verdict','score')}
                row['comparison'] = 'not_comparable' if row['status'] != 'assessed' or row['verdict'] is None or baseline.verdict is None else ('same' if row['verdict']==baseline.verdict else 'different')
                if row['comparison']=='different':
                    row['layer4']['review_required']=True
                    row['layer4']['reasons'].append('legacy_new_disagreement')
            except Exception as exc:
                row['legacy_rule_baseline']={'error':type(exc).__name__}
                row['comparison']='not_comparable'
            results.append(row)
        write_new(out/'results.json', results)
        # Input and output requests are private evidence, not public benchmark artifacts.
        write_new(out/'semantic-requests.json', [dict(task_id=r['task_id'],**q) for r in results for q in r['semantic_requests']])
        if args.live:
            with (out/'semantic-records.jsonl').open('x',encoding='utf8',newline='\n') as f:
                for r in judge.records:
                    f.write(json.dumps(r,ensure_ascii=False)+'\n')
        drift = [p for p,h in frozen.items() if not Path(p).is_file() or sha(p)!=h]
        current_files = {str(p) for p in rd.rglob('*') if p.is_file()}
        locked_files = {p for p in frozen if Path(p).is_relative_to(rd)}
        drift += sorted(current_files-locked_files)
        requests_used = {q['request_sha256'] for r in results for q in r['semantic_requests']}
        extra_replay = set(judge.records)-requests_used if args.replay else set()
        summary = {'format':result_version,'mode':'live' if args.live else 'replay' if args.replay else 'prepare',
                   'tasks':len(results),'evidence_valid':sum(r['layer1']['valid'] for r in results),
                   'assessed':sum(r['status']=='assessed' for r in results),
                   'unassessable':sum(r['status']!='assessed' for r in results),
                   'comparison':dict(collections.Counter(r['comparison'] for r in results)),
                   'distribution':dict(collections.Counter(r['verdict'] for r in results if r['verdict'])),
                   'attempted_requests':judge.calls if judge else 0,'cache_hits':judge.cache_hits if judge else 0,
                   'model':args.model if judge else None,'inputs_unchanged':not drift,
                   'extra_replay_records':len(extra_replay),'formal_quota_accepted':0,'release_authorized':False,
                   'semantic_reliability_validated':False,
                   'limits':['request/citation validity is not semantic truth','prepare never makes semantic passes',
                             'no official scores; explicit authored plan may differ from original rubric',
                             'human review separate; no inherited old kappa','legacy whole-task evidence gate is conservative']}
        if args.outcome_version == 'v2':
            summary.update(task_outcomes=dict(collections.Counter(r['task_outcome'] for r in results)),
                           classification_resolved=sum(r['classification_status']=='resolved' for r in results),
                           legacy_report_compatible=False)
        if args.evidence_policy != 'legacy':
            summary['evidence_policy'] = args.evidence_policy
            summary['dependency_reasons'] = dict(collections.Counter(g['reason'] for r in results
                for g in r['layer1'].get('dependencies', {}).values() if not g['sufficient']))
            if args.evidence_policy == 'scoped-v2':
                summary['limits'][-1] = ('tiered scoped gate: memory conclusions carry evidence tier T0-T3, '
                                         'none of which is independent-instance verification; absent memory evidence (T0) '
                                         'or any contradiction still blocks the task')
                summary['evidence_tiers'] = dict(collections.Counter(
                    g['evidence_tier'] for r in results
                    for g in r['layer1'].get('dependencies', {}).values() if g.get('evidence_tier')))
                summary['artifact_representations'] = dict(collections.Counter(
                    v for r in results for v in r['layer1'].get('artifact_representations', {}).values()))
            else:
                summary['limits'][-1] = 'experimental scoped gate; memory controls/full representation not verified'
        if args.live:
            summary['call_audit'] = judge.audit_summary()
        write_new(out/'review-queue.json', [{'task_id':r['task_id'],'layer4':r['layer4']} for r in results])
        write_new(out/'summary.json', summary)
        if drift or extra_replay:
            raise ValueError('input drift or replay records outside current request set')
        write_new(out/'complete.json', {'summary_sha256':sha(out/'summary.json'),'results_sha256':sha(out/'results.json')})
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0 if summary['assessed']==len(results) else 1
    except Exception as exc:
        # Reports without complete.json are incomplete; do not auto-resume/overwrite.
        # 只报异常类型会把人逼疯（实测：ValueError 后面什么都没有，排查两轮）
        print('Four-layer run rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
