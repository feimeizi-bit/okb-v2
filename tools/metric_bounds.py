#!/usr/bin/env python3
"""okb · 双口径指标 + 检查族拆分（离线分析，不出正式分数/排名）。

- 口径 A（现行）：assessed-only 通过率 = pass/(pass+fail)，unassessable 剔除。
- 口径 B（下界）：conservative = pass/(pass+fail+unassessable)，不可判按失败计。
- 检查族：按 check id 归类（memory-store / artifact / reply / task-application / other），
  只做粗分流的启发式；task-application 族是"回复/产物面上的任务应用检查"，逐条看内容
  绝大多数是跨会话记忆的应用（属构念之内），其中混有少量非记忆的产物质量/一致性检查，
  去留需构念审计（见 docs/benchmark-design-v2.md §7），不要按族名做构念结论。
  统计各维度失败任务的"第一失败族"构成，回答"分差差在记忆面还是言行风格"。

输入：诊断报告 report.json。输出（--out-dir）：
- metric-bounds-v2.json / metric-bounds-v2.md
- report-lower-bound.json（把 assessed_task_pass_rate 换成下界，供 radar_v2 渲染）
退出码：0 / 2。
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

FAMILIES = ('memory-store', 'artifact', 'reply', 'task-application', 'other')
DIMS = ('long_term_retention', 'memory_recall', 'dynamic_update',
        'similar_discrimination', 'boundary_recognition', 'task_reuse')
ZH = {'long_term_retention': '长期保持', 'memory_recall': '记忆调用',
      'dynamic_update': '动态更新', 'similar_discrimination': '相近区分',
      'boundary_recognition': '边界识别', 'task_reuse': '任务复用'}


def family(check_id: str) -> str:
    c = check_id.upper()
    if 'MEM' in c:
        return 'memory-store'
    if 'FILE' in c or 'CONTENT' in c:
        return 'artifact'
    if 'REPLY' in c:
        return 'reply'
    if 'TASK' in c or 'CONF' in c:
        return 'task-application'
    return 'other'


def rate(num, den):
    return round(num / den, 4) if den else None


def first_failure(task):
    for c in task.get('checks') or []:
        if c['result'] == 'contradicted':
            return c['id']
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report', required=True)
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding='utf-8'))
        out_dir = Path(args.out_dir).resolve()
        if report.get('format') != 'okb-diagnostic-report-v2-draft' or not out_dir.is_dir():
            raise ValueError('需要 v2 诊断报告且 out-dir 必须存在')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('metric-bounds rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    tasks = report['tasks']
    agents = sorted({t['agent'] for t in tasks})
    out = dict(format='okb-metric-bounds-v2', run_id=report.get('run_id'),
               plan_sha256=report.get('plan_sha256'),
               note='口径 B 为下界（unassessable 按失败计）；检查族按 check id 归类，仅诊断用',
               agents={}, dimensions={})

    def summarize(sel):
        pass_n = sum(1 for t in sel if t['task_outcome'] == 'pass')
        fail_n = sum(1 for t in sel if t['task_outcome'] == 'fail')
        un_n = sum(1 for t in sel if t['task_outcome'] == 'unassessable')
        fam = collections.Counter()
        spans = collections.Counter()
        for t in sel:
            ff = first_failure(t)
            if ff:
                fam[family(ff)] += 1
            for c in t.get('checks') or []:
                if c['result'] == 'contradicted':
                    spans[family(c['id'])] += 1
        return dict(planned=len(sel), pass_=pass_n, fail=fail_n, unassessable=un_n,
                    rate_assessed=rate(pass_n, pass_n + fail_n),
                    rate_conservative=rate(pass_n, len(sel)),
                    coverage=rate(pass_n + fail_n, len(sel)),
                    first_failure_families={k: fam[k] for k in FAMILIES if fam[k]},
                    contradicted_check_families={k: spans[k] for k in FAMILIES if spans[k]})

    for a in agents:
        out['agents'][a] = summarize([t for t in tasks if t['agent'] == a])
    for d in DIMS:
        row = {}
        for a in agents:
            row[a] = summarize([t for t in tasks if t['agent'] == a and t['dimension'] == d])
        out['dimensions'][d] = row

    (out_dir / 'metric-bounds-v2.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')

    lines = ['# 双口径指标 + 检查族拆分（v2）', '',
             '- 口径 A=assessed-only；口径 B=unassessable 按失败（下界）；检查族=按 check id 归类。',
             '- 绑定：plan `%s…` · run `%s`' % ((report.get('plan_sha256') or '')[:12], report.get('run_id')),
             '', '## 总体', '',
             '| Agent | 计划 | 通过 | 失败 | 不可判 | 口径A | 口径B | 覆盖率 | 第一失败族构成 |', '|---|---|---|---|---|---|---|---|---|']
    for a in agents:
        x = out['agents'][a]
        lines.append('| %s | %d | %d | %d | %d | %s | %s | %s | %s |' % (
            a, x['planned'], x['pass_'], x['fail'], x['unassessable'],
            x['rate_assessed'], x['rate_conservative'], x['coverage'],
            json.dumps(x['first_failure_families'], ensure_ascii=False)))
    lines += ['', '## 分维度（口径A / 口径B / 覆盖率 / 第一失败族）', '',
              '| 维度 | Agent | A | B | 覆盖 | 失败族 |', '|---|---|---|---|---|---|']
    for d in DIMS:
        for a in agents:
            x = out['dimensions'][d][a]
            lines.append('| %s | %s | %s | %s | %s | %s |' % (
                ZH[d], a, x['rate_assessed'], x['rate_conservative'], x['coverage'],
                json.dumps(x['first_failure_families'], ensure_ascii=False)))
    (out_dir / 'metric-bounds-v2.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    # 下界报告（供 radar_v2 渲染）：仅替换 assessed_task_pass_rate
    derived = json.loads(json.dumps(report))
    for d in DIMS:
        for a in agents:
            metric = derived['dimensions'][d]['agents'][a]
            metric['assessed_task_pass_rate'] = out['dimensions'][d][a]['rate_conservative']
    derived['metric_variant'] = 'lower_bound_unassessable_as_fail'
    (out_dir / 'report-lower-bound.json').write_text(
        json.dumps(derived, ensure_ascii=False, indent=1), encoding='utf-8')

    print(json.dumps(dict(out=str(out_dir), agents={a: out['agents'][a]['rate_assessed'] for a in agents},
                          agents_conservative={a: out['agents'][a]['rate_conservative'] for a in agents}),
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
