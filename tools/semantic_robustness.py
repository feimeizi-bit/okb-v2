#!/usr/bin/env python3
"""Prepare or evaluate AUTHOR-DESIGNED semantic contrast cases; not held-out accuracy.
No expected labels enter the judge request. No calls unless --live + authorization.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from scoring.fact_judge import request, fingerprint, strict_json, ReplayFacts, LiveFacts, validate_response
from score_four_layer import write_new, sha


def build_requests(corpus):
    if corpus.get('format')!='okb-semantic-robustness-v1' or corpus.get('case_count')!=len(corpus.get('cases',[])) or not corpus.get('cases'):
        raise ValueError('invalid corpus')
    seen=set();out=[]
    for c in corpus['cases']:
        if set(c)!={'id','requirement','context','observations','expected','category','equivalence_group'}:
            raise ValueError('invalid case fields')
        if c['id'] in seen or c['expected'] not in {'supported','contradicted','insufficient'}:
            raise ValueError('duplicate id or invalid expectation')
        seen.add(c['id'])
        req=request(c['requirement'],c['context'],c['observations'])
        out.append({'case_id':c['id'],'request_sha256':fingerprint(req),'request':req})
    if len({r['request_sha256'] for r in out})!=len(out):
        raise ValueError('duplicate semantic requests')
    return out


def metrics(cases, results):
    if {r['case_id'] for r in results}!={c['id'] for c in cases} or len(results)!=len(cases):
        raise ValueError('result coverage mismatch')
    by={r['case_id']:r for r in results};valid=[c for c in cases if by[c['id']]['valid_response']]
    positive=[c for c in valid if c['expected']=='supported']
    negative=[c for c in valid if c['expected']=='contradicted']
    undecidable=[c for c in valid if c['expected']=='insufficient']
    ratio=lambda n,d: {'numerator':n,'denominator':d,'value':n/d if d else None}
    groups=collections.defaultdict(list)
    for c in cases:
        if c['equivalence_group']:groups[c['equivalence_group']].append(c)
    complete_groups=[cs for cs in groups.values() if len(cs)>1 and all(by[c['id']]['valid_response'] for c in cs)]
    return {'total_cases':len(cases),'valid_responses':len(valid),
        'response_coverage':ratio(len(valid),len(cases)),
        'exact_expected_match':ratio(sum(by[c['id']]['result']==c['expected'] for c in valid),len(valid)),
        'false_rejection':ratio(sum(by[c['id']]['result']=='contradicted' for c in positive),len(positive)),
        'false_acceptance':ratio(sum(by[c['id']]['result']=='supported' for c in negative),len(negative)),
        'unsupported_decision':ratio(sum(by[c['id']]['result']!='insufficient' for c in undecidable),len(undecidable)),
        'semantic_abstention':ratio(sum(by[c['id']]['result']=='insufficient' for c in valid),len(valid)),
        'transport_or_validation_missing':ratio(len(cases)-len(valid),len(cases)),
        'equivalence_consistency':ratio(sum(len({by[c['id']]['result'] for c in cs})==1 for cs in complete_groups),len(complete_groups)),
        'equivalence_all_correct':ratio(sum(all(by[c['id']]['result']==c['expected'] for c in cs) for cs in complete_groups),len(complete_groups)),
        'category_counts':dict(collections.Counter(c['category'] for c in cases))}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--corpus',default=str(ROOT/'samples/semantic-robustness.v1.json'))
    ap.add_argument('--out-dir',required=True)
    group=ap.add_mutually_exclusive_group();group.add_argument('--replay');group.add_argument('--live',action='store_true')
    ap.add_argument('--model');ap.add_argument('--base-url');ap.add_argument('--max-calls',type=int)
    ap.add_argument('--max-output-tokens',type=int);ap.add_argument('--budget-file')
    ap.add_argument('--runtime-contract-file',help='Cost-bound runtime contract; required for live')
    ap.add_argument('--cost-group-file',help='Path-bound cumulative judge group; required for live')
    ap.add_argument('--cost-scope',help='Exact declared scope identifier; required for live')
    from scoring.cost_review import WIRE_PROFILES
    ap.add_argument('--wire-profile',choices=WIRE_PROFILES,default='legacy-chat-v1')
    ap.add_argument('--reasoning-effort',choices=['low','medium','high','xhigh','max'])
    ap.add_argument('--api-key-env',default='OKB_FACT_JUDGE_API_KEY');ap.add_argument('--authorize-paid-calls',action='store_true')
    a=ap.parse_args(argv)
    try:
        src=Path(a.corpus).resolve();out=Path(a.out_dir).resolve()
        inputs=[src]+([Path(a.replay).resolve()] if a.replay else [])
        if a.budget_file:inputs.append(Path(a.budget_file).resolve())
        if a.runtime_contract_file:inputs.append(Path(a.runtime_contract_file).resolve())
        if a.cost_group_file:inputs.append(Path(a.cost_group_file).resolve())
        if out.exists() or not out.parent.is_dir() or any(p.is_relative_to(out) for p in inputs):
            raise ValueError('output must be new with existing parent, not overlapping input')
        if a.live and not a.authorize_paid_calls:raise ValueError('paid authorization required')
        if not a.live and (a.authorize_paid_calls or a.base_url or a.max_calls is not None or a.budget_file or a.runtime_contract_file or a.cost_group_file or a.cost_scope or a.max_output_tokens is not None):raise ValueError('live options in offline mode')
        if not a.live and (a.wire_profile != 'legacy-chat-v1' or a.reasoning_effort is not None):raise ValueError('wire profile options require live mode')
        if (a.live or a.replay) and not a.model:raise ValueError('model required')
        if a.live:
            from scoring.audited_facts import AuditedFacts, validate_budget
            if not a.budget_file or type(a.max_output_tokens) is not int or not 1 <= a.max_output_tokens <= 32768:
                raise ValueError('live mode needs budget file and output limit')
            budget=validate_budget(strict_json(Path(a.budget_file).read_text(encoding='utf8')))
            if a.model == 'gpt-6.1-sol' and (a.wire_profile != 'openai-cache-chat-v1' or budget.get('format') != 'okb-judge-budget-v2-draft'):raise ValueError('current GPT live calls require the four-bucket cache billing profile')
            if not a.runtime_contract_file:raise ValueError('live mode needs a cost-bound runtime contract')
            contract=strict_json(Path(a.runtime_contract_file).read_text(encoding='utf8'))
            if not a.cost_group_file or not a.cost_scope:raise ValueError('live mode needs a cumulative cost group and exact scope')
            from scoring.cost_group import CostGroup
            cost_group=CostGroup(strict_json(Path(a.cost_group_file).read_text(encoding='utf8')))
            cost_group.validate_scope(a.cost_scope,contract)
            if any(Path(cost_group.policy['ledger_path']) == path for path in inputs):raise ValueError('mutable cost ledger overlaps input')
        locked={str(p):sha(p) for p in inputs}
        corpus=strict_json(src.read_text(encoding='utf8'));requests=build_requests(corpus)
        if a.live:
            from scoring.cost_runtime import validate_runtime_contract
            reviewed_requests=[dict(row,source=dict(kind='author_development_case',case_id=row['case_id'])) for row in requests]
            validate_runtime_contract(contract,reviewed_requests,model=a.model,base_url=a.base_url,
                max_output_tokens=a.max_output_tokens,budget=budget,max_calls=a.max_calls,
                wire_profile=a.wire_profile,reasoning_effort=a.reasoning_effort)
        judge=None
        if a.replay:
            judge=ReplayFacts([strict_json(s) for s in Path(a.replay).read_text(encoding='utf8').splitlines() if s.strip()],a.model)
            if set(judge.records)-{r['request_sha256'] for r in requests}:raise ValueError('extra replay records')
        elif a.live:
            judge=LiveFacts(base_url=a.base_url or '',api_key=os.environ.get(a.api_key_env,''),model=a.model,max_calls=a.max_calls)
        out.mkdir();write_new(out/'input-lock.json',locked);write_new(out/'requests.json',requests)
        if a.live:
            judge=AuditedFacts(base_url=a.base_url,api_key=os.environ.get(a.api_key_env,''),model=a.model,
                max_calls=a.max_calls,max_output_tokens=a.max_output_tokens,budget=budget,
                journal_path=out/'call-journal.jsonl',runtime_contract=contract,reviewed_requests=reviewed_requests,
                wire_profile=a.wire_profile,reasoning_effort=a.reasoning_effort,
                cost_group=cost_group,cost_scope=a.cost_scope)
        results=[]
        for r in requests:
            obj={'case_id':r['case_id'],'request_sha256':r['request_sha256'],'result':None,'valid_response':False}
            if judge:
                try:
                    resp=validate_response(r['request'],judge.judge(r['request']))
                    obj.update(result=resp['result'],response=resp,valid_response=True)
                except Exception as exc:obj['error']=type(exc).__name__
            else:obj['error']='not_executed'
            results.append(obj)
        report=metrics(corpus['cases'],results)
        report.update(mode='live' if a.live else 'replay' if a.replay else 'prepare',model=a.model if judge else None,
                      attempted_requests=judge.calls if judge else 0,cache_hits=judge.cache_hits if judge else 0,
                      corpus_sha256=locked[str(src)],semantic_reliability_validated=False,
                      limitations=['author development cases, not independent gold or held-out validation',
                                   'citation structure correctness is not semantic correctness',
                                   'missing calls are not semantic abstentions or capability failures',
                                   'metrics exclude missing responses but always report coverage',
                                   'same answer across paraphrases can be consistently wrong'])
        if a.live:report['call_audit']=judge.audit_summary()
        write_new(out/'results.json',results);write_new(out/'summary.json',report)
        if a.live:
            with (out/'semantic-records.jsonl').open('x',encoding='utf8',newline='\n') as f:
                for r in judge.records:f.write(json.dumps(r,ensure_ascii=False)+'\n')
        if any(sha(Path(p))!=h for p,h in locked.items()):raise ValueError('input changed')
        write_new(out/'complete.json',{'summary_sha256':sha(out/'summary.json'),'results_sha256':sha(out/'results.json')})
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report['valid_responses']==len(results) else 1
    except Exception as exc:
        print('Semantic robustness rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
