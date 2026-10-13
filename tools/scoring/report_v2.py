"""Read-only diagnostic reporting for explicit v2 score bundles; no ranking."""
import collections
import hashlib
import html
import json
import re
from pathlib import Path

from .fact_judge import strict_json
from .four_layer import validate_plan
from .outcome_v2 import aggregate

DIMS = ('long_term_retention', 'memory_recall', 'dynamic_update',
        'similar_discrimination', 'boundary_recognition', 'task_reuse')
VERSION = 'okb-diagnostic-report-v2-draft'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_bundle(score_dir, run_dir, paper_path, plan_path, *, verify_trace=False):
    """Require completed, unchanged local inputs and recompute result accounting.

    Fingerprints check consistency only, not authorship or semantic truth.
    Legacy score files are deliberately rejected instead of guessing a migration.
    """
    score_dir, run_dir, paper_path, plan_path = map(Path, (score_dir, run_dir, paper_path, plan_path))
    paths = [score_dir / n for n in ('complete.json', 'results.json', 'summary.json', 'input-lock.json')]
    snapshots = {p.resolve(): p.read_bytes() for p in paths}
    complete, rows, summary, lock = [strict_json(snapshots[p.resolve()].decode('utf8')) for p in paths]
    if set(complete) != {'summary_sha256', 'results_sha256'} or any(
            complete[n + '_sha256'] != sha(snapshots[(score_dir / (n + '.json')).resolve()])
            for n in ('summary', 'results')):
        raise ValueError('score completion fingerprint mismatch')
    if summary.get('format') != 'okb-four-layer-v2' or lock.get('format') != 'okb-four-layer-v2':
        raise ValueError('report needs explicit v2 result bundle')
    if summary.get('inputs_unchanged') is not True or summary.get('release_authorized') is not False or summary.get('semantic_reliability_validated') is not False:
        raise ValueError('unsupported score publication/validation state')
    policy = summary.get('evidence_policy', 'legacy')
    if policy not in ('legacy', 'scoped-v1', 'scoped-v2') or lock.get('evidence_policy', 'legacy') != policy:
        raise ValueError('evidence policy mismatch')
    if summary.get('mode') not in ('prepare', 'replay', 'live'):
        raise ValueError('unsupported score mode')
    frozen = lock.get('sha256')
    if not isinstance(frozen, dict) or not frozen:
        raise ValueError('missing source lock')
    normalized = {}
    for name, digest in frozen.items():
        p = Path(name).resolve()
        if p in normalized or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('invalid or duplicate locked path')
        data = p.read_bytes()
        if sha(data) != digest:
            raise ValueError('locked input changed')
        normalized[p] = digest
        snapshots[p] = data
    source_paths = [paper_path.resolve(), plan_path.resolve(), (run_dir/'manifest.json').resolve(), (run_dir/'evidence.jsonl').resolve()]
    if any(p not in normalized for p in source_paths):
        raise ValueError('explicit source not bound by score lock')
    actual_run_files = {p.resolve() for p in run_dir.rglob('*') if p.is_file()}
    locked_run_files = {p for p in normalized if p.is_relative_to(run_dir.resolve())}
    if actual_run_files != locked_run_files:
        raise ValueError('run file set changed')
    paper = [strict_json(s) for s in snapshots[source_paths[0]].decode('utf8').splitlines() if s.strip()]
    plan = strict_json(snapshots[source_paths[1]].decode('utf8'))
    validate_plan(plan, paper, normalized[source_paths[0]])
    if lock.get('plan_sha256') != normalized[source_paths[1]]:
        raise ValueError('plan lock mismatch')
    manifest = strict_json(snapshots[source_paths[2]].decode('utf8'))
    evidence = [strict_json(s) for s in snapshots[source_paths[3]].decode('utf8').splitlines() if s.strip()]
    selected = {s['sample_id']: s for s in plan['samples']}
    samples = {s['id']: s for s in paper}
    if len(samples) != len(paper) or any(samples[s]['dimension'] not in DIMS for s in selected):
        raise ValueError('duplicate sample or unsupported dimension')
    agents = {a['name']: a for a in manifest['agents']}
    if len(agents) != len(manifest['agents']) or not agents or any(not a for a in agents):
        raise ValueError('invalid agent identities')
    expected, all_tasks, units = {}, set(), set()
    for t in manifest['tasks']:
        tid = t['task_id']
        if tid in all_tasks:
            raise ValueError('duplicate manifest task')
        all_tasks.add(tid)
        candidates = [a for a in agents if tid.startswith(a + '-')]
        if not candidates:
            raise ValueError('unknown manifest agent')
        agent = max(candidates, key=len)
        suffix = tid[len(agent)+1:]
        sid = suffix if suffix in samples else re.sub(r'-r\d+$', '', suffix)
        if sid not in selected:
            continue
        repeat = suffix[len(sid):]
        unit = (agent, sid, repeat)
        if unit in units:
            raise ValueError('duplicate comparison unit')
        units.add(unit)
        expected[tid] = (agent, sid, repeat, t)
    if not isinstance(rows, list) or not rows or len({r['task_id'] for r in rows}) != len(rows) or {r['task_id'] for r in rows} != set(expected):
        raise ValueError('missing, extra or duplicate score task')
    if {r['sample_id'] for r in rows} != set(selected):
        raise ValueError('selected plan sample missing')
    ev = {e['id']: e for e in evidence}
    if len(ev) != len(evidence):
        raise ValueError('duplicate evidence id')
    tasks = []
    for row in rows:
        agent, sid, repeat, original = expected[row['task_id']]
        if row['sample_id'] != sid or row.get('version') != 'okb-four-layer-v2' or row.get('plan_sha256') != lock['plan_sha256'] or row.get('legacy_report_compatible') is not False:
            raise ValueError('task version or identity mismatch')
        checks = row['layer2'] + row['layer3']
        vector = {c['id']: c for c in checks}
        definitions = selected[sid]['checks']
        if len(vector) != len(checks):
            raise ValueError('duplicate result check')
        valid = row['layer1']['valid']
        if valid:
            if set(vector) != {c['id'] for c in definitions}:
                raise ValueError('missing or extra mandatory check')
            for d in definitions:
                c = vector[d['id']]
                if c['layer'] != d['layer'] or c['on_failure'] != d['on_failure'] or c not in row['layer2' if d['layer']=='objective' else 'layer3']:
                    raise ValueError('check definition mismatch')
        elif checks:
            raise ValueError('invalid source has scored checks')
        axes = aggregate(valid, [vector[d['id']] for d in definitions] if valid else [])
        if any(row.get(k) != v for k, v in axes.items()) or type(row['score']) not in (int, type(None)):
            raise ValueError('dual-axis accounting mismatch')
        if row['status'] != ('unassessable' if axes['task_outcome']=='unassessable' else 'assessed') or row['verdict'] != axes['error_class']:
            raise ValueError('status or verdict mismatch')
        if summary['mode'] == 'prepare' and any(c['result'] != 'insufficient' for c in row['layer3']):
            raise ValueError('prepare contains a semantic verdict')
        refs = row['evidence_refs']
        if not isinstance(refs, list) or len(set(refs)) != len(refs) or any(
                r not in ev or ev[r]['task_id'] != row['task_id'] or ev[r]['run_id'] != manifest['run_id'] for r in refs):
            raise ValueError('broken or cross-task evidence reference')
        tasks.append(dict(task_id=row['task_id'], agent=agent, sample_id=sid, repeat=repeat,
            dimension=samples[sid]['dimension'], execution_status=original.get('status'),
            duration_ms=original.get('duration_ms'), **axes,
            layer1=row['layer1'], checks=checks, check_definitions=definitions,
            comparison=row.get('comparison'), evidence_refs=refs,
            evidence=[{'id':r, 'source':ev[r]['source'], 'type':ev[r]['type'], 'payload':ev[r]['payload']} for r in refs],
            script=samples[sid]['script']))
    counts = collections.Counter(t['task_outcome'] for t in tasks)
    expected_summary = dict(tasks=len(tasks), evidence_valid=sum(r['layer1']['valid'] for r in rows),
        assessed=counts['pass']+counts['fail'], unassessable=counts['unassessable'],
        task_outcomes=dict(counts), classification_resolved=sum(t['classification_status']=='resolved' for t in tasks))
    if any(summary.get(k) != v for k,v in expected_summary.items()):
        raise ValueError('summary accounting mismatch')
    bundle = dict(tasks=tasks, agents=agents, selected=selected, samples=samples,
        run_id=manifest['run_id'], summary=summary, lock=lock)
    if verify_trace:
        from .report_trace import verify_traces
        verify_traces(bundle, rows, run_dir)
    return bundle, snapshots, actual_run_files


def metric(tasks, planned_samples):
    counts = {k:sum(t['task_outcome']==k for t in tasks) for k in ('pass','fail','unassessable')}
    by_sample = collections.defaultdict(list)
    for t in tasks:
        by_sample[t['sample_id']].append(t)
    # A repeated sample is fully assessable only when every planned repeat is.
    complete = {s:ts for s,ts in by_sample.items() if all(t['score'] is not None for t in ts)}
    means = [sum(t['score'] for t in ts)/len(ts) for ts in complete.values()]
    return dict(planned_tasks=len(tasks), planned_samples=planned_samples, **counts,
        fully_assessable_samples=len(complete),
        sample_coverage=len(complete)/planned_samples if planned_samples else None,
        assessed_task_pass_rate=counts['pass']/(counts['pass']+counts['fail']) if counts['pass']+counts['fail'] else None,
        complete_sample_mean=sum(means)/len(means) if means else None,
        unresolved_classification=sum(t['classification_status']=='unresolved' for t in tasks),
        error_classes=dict(collections.Counter(t['error_class'] for t in tasks if t['error_class'] is not None)))


TIER_ORDER = ('T0_absent_or_error', 'T1_declared_snapshot', 'T2_bound_snapshot', 'T3_controlled_capture')
TIER_LABEL = {'T0_absent_or_error': 'T0 缺失/异常（不判）',
              'T1_declared_snapshot': 'T1 仅声明快照（未绑定观测）',
              'T2_bound_snapshot': 'T2 快照已绑定回执或全文采集',
              'T3_controlled_capture': 'T3 回执完整且全文表示已验证'}


def evidence_profile(tasks):
    """Weakest-link evidence level per task, then aggregated.

    A task is only as strong as its least-supported check, and no tier here is
    independent-instance verification of the memory store: T3 means the observation
    receipt and full capture agree, not that the store was verified out of band.
    """
    for task in tasks:
        tiers = [c['evidence_tier'] for c in task['checks'] if c.get('evidence_tier')]
        task['weakest_evidence_tier'] = min(tiers, key=TIER_ORDER.index) if tiers else None
        task['weakest_evidence_label'] = TIER_LABEL.get(task['weakest_evidence_tier'])
    task_level = collections.Counter(x for x in (t['weakest_evidence_tier'] for t in tasks) if x)
    check_level = collections.Counter(c['evidence_tier'] for t in tasks for c in t['checks'] if c.get('evidence_tier'))
    by_agent = {a: dict(collections.Counter(t['weakest_evidence_tier'] for t in tasks
                    if t['agent'] == a and t['weakest_evidence_tier'])) for a in sorted({t['agent'] for t in tasks})}
    representations = collections.Counter(v for t in tasks
                                          for v in (t['layer1'].get('artifact_representations') or {}).values())
    return dict(order=list(TIER_ORDER), labels=TIER_LABEL, task_level=dict(task_level),
                check_level=dict(check_level), by_agent=by_agent,
                artifact_representations=dict(representations),
                independent_instance_verified=False,
                note='tiers describe how bound to the run the memory snapshot is; none is out-of-band store verification')


def build_report(bundle, attribution=None):
    """attribution：基线归因读数（tools/baseline_attribution.py 的产物）。

    它是**诊断量**：只放进独立一节，绝不进 dimensions、不参与 score/pass-rate/排名。
    没给就如实写 not_computed 并说明缺什么，而不是留空让读者以为"这项没问题"。
    """
    tasks = sorted(bundle['tasks'], key=lambda t:(t['agent'],t['dimension'],t['sample_id'],t['repeat']))
    # Keep declared agents even if their selected tasks are absent from the run.
    agents = sorted(bundle['agents'])
    dimensions = {}
    for dim in DIMS:
        selected = {s for s in bundle['selected'] if bundle['samples'][s]['dimension']==dim}
        per_agent = {a:metric([t for t in tasks if t['agent']==a and t['dimension']==dim],len(selected)) for a in agents}
        planned = {a:{(t['sample_id'],t['repeat']) for t in tasks if t['agent']==a and t['dimension']==dim} for a in agents}
        assessed = {a:{(t['sample_id'],t['repeat']) for t in tasks if t['agent']==a and t['dimension']==dim and t['score'] is not None} for a in agents}
        common_planned = set.intersection(*planned.values()) if len(agents)>1 else set()
        common = set.intersection(*assessed.values()) if len(agents)>1 else set()
        common_rates = {}
        for a in agents:
            vals=[t['score'] for t in tasks if t['agent']==a and t['dimension']==dim and (t['sample_id'],t['repeat']) in common]
            common_rates[a]=sum(vals)/len(vals) if vals else None
        dimensions[dim]=dict(agents=per_agent,
            missing_sample_tasks={a:sorted(selected-{s for s,r in planned[a]}) for a in agents},
            common_planned_units=[list(x) for x in sorted(common_planned)],
            common_assessable_units=[list(x) for x in sorted(common)], common_unit_pass_rates=common_rates,
            common_excluded=[dict(sample_id=s,repeat=r,unavailable_agents=[a for a in agents if (s,r) not in assessed[a]]) for s,r in sorted(set.union(*planned.values())-common)],
            same_planned_units=len(agents)>1 and all(v==planned[agents[0]] for v in planned.values()))
    return dict(format=VERSION,run_id=bundle['run_id'],mode=bundle['summary']['mode'],
        evidence_policy=bundle['summary'].get('evidence_policy','legacy'), plan_sha256=bundle['lock']['plan_sha256'],
        release_authorized=False, semantic_reliability_validated=False, overall_score=None, ranking=None,
        limits=['Exploratory diagnostic report; no formal six-dimension score or ranking.',
                'Missing and unresolved values remain null, not zero.',
                'Repeated tasks are not independent samples; common units are sample plus repeat.',
                'Rates use explicit subsets; different denominators cannot establish a ranking.',
                'Fingerprints verify consistency, not semantic truth or independent provenance.',
                'Under scoped-v2 memory conclusions are graded T0-T3 instead of vetoed; T0 stays unassessable and no tier claims out-of-band memory-store verification.',
                'Private report contains task/evidence text; review before sharing.'],
        agents=[dict(name=a,agent_kind=bundle['agents'][a].get('agent_kind'),
                     model=bundle['agents'][a].get('model'),temperature=bundle['agents'][a].get('temperature')) for a in agents],
        dimensions=dimensions,tasks=tasks,evidence_profile=evidence_profile(tasks),
        trace_verification=bundle.get('trace_verification', {'status':'not_requested'}),
        review_tasks=[t['task_id'] for t in tasks if t['task_outcome']!='pass' or t['classification_status']=='unresolved' or t['comparison']=='different'],
        attribution=attribution or dict(
            status='not_computed',
            reason='调用方未提供归因：需要成对 before/after 记忆快照 + 计划内含 memory_before 检查项'
                   '（okb baseline-attribution --run-dir <runs/..> --plan <plan> 可单独产出）'),
        judge_call_audit=bundle['summary'].get('call_audit'), total_cost=None,
        cost_status='unknown: no independently reconciled total Agent and judge billing')


def render_html(report):
    esc=lambda value: html.escape(str(value),quote=True)
    pretty=lambda value: esc(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
    rate=lambda value: 'N/A' if value is None else f'{value:.1%}'
    labels=dict(zip(DIMS,('长期保持','记忆调用','动态更新','相近区分','边界识别','任务复用')))
    out=['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
         '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
         '<title>四层评分诊断报告</title><style>body{font:16px system-ui;max-width:1150px;margin:32px auto;padding:0 20px;color:#20252b}table{border-collapse:collapse;width:100%;margin:18px 0}th,td{padding:9px;border:1px solid #ccd2d8;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px}details{margin:16px 0}a{color:#135ca3}</style>',
         '<h1>四层评分诊断报告</h1><p>探索性结果；无正式总分或排名。N/A 表示不可判或没有样本，不计为零。</p>',
         '<p>运行：'+esc(report['run_id'])+'；模式：'+esc(report['mode'])+'；证据策略：'+esc(report['evidence_policy'])+'</p>',
         '<h2>六维覆盖与结果</h2><p>样本覆盖要求该样本所有计划重复均可判。任务通过率仅使用可判任务；重复任务不增加独立样本数。</p>',
         '<table><tr><th>维度 / Agent</th><th>任务：通过 / 失败 / 未决</th><th>完整可判样本 / 计划样本</th><th>样本覆盖</th><th>可判任务通过率</th><th>类别未定</th></tr>']
    for dim,d in report['dimensions'].items():
        for a,m in d['agents'].items():
            out.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (labels[dim]+' / '+a,f"{m['pass']} / {m['fail']} / {m['unassessable']}",f"{m['fully_assessable_samples']} / {m['planned_samples']}",rate(m['sample_coverage']),rate(m['assessed_task_pass_rate']),m['unresolved_classification']))+'</tr>')
    profile = report.get('evidence_profile') or {}
    out.append('</table>')
    if profile:
        out.append('<h2>证据等级（scoping 分层，不是一票否决）</h2>'
                   '<p>任务等级取该任务所有检查项中最弱的一档；T0 仍然不判，T1-T3 参与评分并在结论上标注等级。'
                   '任何等级都不等于对记忆存储做过带外独立验证。</p>'
                   '<table><tr><th>等级</th><th>含义</th><th>任务数</th><th>检查项数</th></tr>')
        for tier in profile.get('order', ()):
            out.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in
                       (tier, profile.get('labels', {}).get(tier), profile.get('task_level', {}).get(tier, 0),
                        profile.get('check_level', {}).get(tier, 0)))+'</tr>')
        out.append('</table><p>按智能体：'+esc(json.dumps(profile.get('by_agent'), ensure_ascii=False))
                   +'；产物来源表示：'+esc(json.dumps(profile.get('artifact_representations'), ensure_ascii=False))+'</p>')
    att = report.get('attribution') or {}
    if att.get('status') == 'computed':
        out.extend(['<h2>写入归因（诊断量，不计入六维与排名）</h2>',
                    '<p>把 <code>omitted</code> 拆成"记忆面从来没有过"（写入/准入失效）与'
                    '"写过又丢了"（保留失效）：只看 before 快照条目数、本题写入事件数、after 快照条目数，'
                    '<strong>不做正文或关键词匹配</strong>。</p>',
                    '<pre>' + pretty({k: att[k] for k in
                        ('baseline_samples', 'tasks_with_both_snapshots', 'tasks_without_pair',
                         'attribution_counts', 'attribution_rates', 'never_written_share',
                         'unattributable_share', 'limits')}) + '</pre>',
                    '<details><summary>逐任务归因行</summary><pre>' + pretty(att.get('rows', [])) +
                    '</pre></details>'])
    else:
        out.extend(['<h2>写入归因</h2>', '<p><code>' + esc(att.get('status', 'absent')) +
                    '</code>：' + esc(att.get('reason', '该题卷不含 memory_before 基线检查项')) + '</p>'])
    out.extend(['<details><summary>共同可判子集、排除名单及样本均值</summary><pre>'+pretty(report['dimensions'])+'</pre></details>',
        '<details><summary>配置、费用与局限</summary><pre>'+pretty({k:report[k] for k in ('agents','limits','judge_call_audit','total_cost','cost_status','plan_sha256')})+'</pre></details>',
        '<h2>全部任务与逐项证据</h2><p>追溯校验：'+esc(report['trace_verification']['status'])+
        '。引文位置只验证来源，不证明语义判断正确。未启用追溯时，检查编号与原始证据之间未重新核对。</p><ul>'])
    for i,t in enumerate(report['tasks']):
        out.append(f'<li><a href="#task-{i}">'+esc(t['task_id'])+'</a> — '+esc(t['task_outcome'])+'</li>')
    out.append('</ul>')
    for i,t in enumerate(report['tasks']):
        out.append(f'<details id="task-{i}"><summary>'+esc(t['task_id'])+' — '+esc(t['task_outcome'])+' / '+esc(t['error_class'] or '类别未定')+'</summary>')
        traces=t.get('trace',{}).get('checks',[])
        if traces:
            out.append('<p>逐项定位：'+ ' · '.join(f'<a href="#task-{i}-check-{j}">'+esc(c['check_id'])+'</a>' for j,c in enumerate(traces))+'</p>')
        out.append('<pre>'+pretty({k:v for k,v in t.items() if k!='trace'})+'</pre>')
        for j,c in enumerate(traces):
            out.append(f'<section id="task-{i}-check-{j}"><h3>'+esc(c['check_id'])+' — '+esc(c['status'])+'</h3><p>pointer 指向下方投影内容；raw_ref 是来源文件定位，不能将派生文件状态当成原始文件逐字引文。</p><pre>'+pretty(c)+'</pre></section>')
        if t.get('trace',{}).get('status')=='source_invalid':
            out.append('<p>来源无效，未生成检查项定位：'+esc(t['trace']['reason'])+'</p>')
        out.append('</details>')
    return '\n'.join(out+['</html>'])+'\n'
