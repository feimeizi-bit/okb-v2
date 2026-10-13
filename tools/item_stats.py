#!/usr/bin/env python3
"""okb · item 难度/区分度台账（离线分析，不出正式分数）。

口径（本工具为唯一定义处；旧 QC 报告里未定义的 "T1−T2≥0.6" 占位由本口径替代）：
- 难度 = 该题跨两 Agent 的有效任务通过率（assessed 口径）。
- 区分度 = |openclaw 通过率 − hermes 通过率|（assessed 口径），≥0.6 记高区分。
- 饱和 = 双方 assessed 全过且无失败/不可判（天花板题，需复核是否过易）。
- 地板 = 双方 assessed 全挂（需复核是否过难/判据过严）。
- 波动 = 同一 Agent 三次重复（r1~r3 全 assessed 时）结论不一致。

输入：score 结果 results.json + 计划 plan.json（绑定 plan_sha256）。
输出：--out-dir 下 item-stats-v2.json / item-stats-v2.md。
退出码：0 成功 / 2 输入不可读或版本不匹配。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

DIMS = ('long_term_retention', 'memory_recall', 'dynamic_update',
        'similar_discrimination', 'boundary_recognition', 'task_reuse')
ZH = {'long_term_retention': '长期保持', 'memory_recall': '记忆调用',
      'dynamic_update': '动态更新', 'similar_discrimination': '相近区分',
      'boundary_recognition': '边界识别', 'task_reuse': '任务复用'}
HIGH_DISC = 0.6


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


def first_failure(task):
    for c in task['layer2'] + task['layer3']:
        if c['result'] == 'contradicted':
            return c['id']
    return None


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--results', required=True)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--paper', required=True, help='built paper jsonl（含 dimension）')
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args(argv)
    try:
        results_path, plan_path = Path(args.results).resolve(), Path(args.plan).resolve()
        paper_rows = [json.loads(l) for l in Path(args.paper).read_text(encoding='utf-8').splitlines() if l.strip()]
        rows = json.loads(results_path.read_text(encoding='utf-8'))
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        out_dir = Path(args.out_dir).resolve()
        if not out_dir.is_dir():
            raise ValueError('out-dir must exist')
        plan_sha = sha(plan_path)
        if any(r.get('plan_sha256') != plan_sha for r in rows):
            raise ValueError('results 未绑定当前 plan sha（拿旧批次算新台账？）')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('item-stats rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    dim_of = {s['id']: s['dimension'] for s in paper_rows}
    by_sample = collections.defaultdict(lambda: {'openclaw': [], 'hermes': []})
    for r in rows:
        agent = r['task_id'].rsplit('-r', 1)[0]
        agent = 'openclaw' if agent.startswith('openclaw-') else 'hermes' if agent.startswith('hermes-') else agent
        by_sample[r['sample_id']][agent].append(r)

    items = []
    for sid, agents in sorted(by_sample.items()):
        def stat(agent_rows):
            assessed = [r for r in agent_rows if r.get('score') is not None]
            passed = sum(1 for r in assessed if r['score'] == 1)
            outcomes = [r.get('verdict') or r.get('status') for r in agent_rows]
            assessed_outcomes = [r.get('verdict') for r in assessed]
            return dict(
                planned=len(agent_rows), assessed=len(assessed), passed=passed,
                failed=len(assessed) - passed, unassessable=len(agent_rows) - len(assessed),
                pass_rate_assessed=round(passed / len(assessed), 4) if assessed else None,
                pass_rate_conservative=round(passed / len(agent_rows), 4) if agent_rows else None,
                repeat_flip=bool(len(set(assessed_outcomes)) > 1 and len(assessed) >= 2),
                outcomes=outcomes)
        a, b = stat(agents['openclaw']), stat(agents['hermes'])
        paired = [x for x in (a['pass_rate_assessed'], b['pass_rate_assessed']) if x is not None]
        difficulty = round(sum(paired) / len(paired), 4) if paired else None
        disc = (round(abs(a['pass_rate_assessed'] - b['pass_rate_assessed']), 4)
                if a['pass_rate_assessed'] is not None and b['pass_rate_assessed'] is not None else None)
        fails = collections.Counter()
        for ag in ('openclaw', 'hermes'):
            for r in agents[ag]:
                ff = first_failure(r)
                if ff:
                    fails[family(ff)] += 1
        dim = dim_of.get(sid)
        items.append(dict(
            sample_id=sid, dimension=dim, difficulty=difficulty, discrimination=disc,
            saturated=bool(a['pass_rate_assessed'] == 1 and b['pass_rate_assessed'] == 1
                           and a['unassessable'] == 0 and b['unassessable'] == 0),
            floor=bool(a['pass_rate_assessed'] == 0 and b['pass_rate_assessed'] == 0
                       and a['assessed'] and b['assessed']),
            any_repeat_flip=bool(a['repeat_flip'] or b['repeat_flip']),
            first_failure_families=dict(fails),
            openclaw=a, hermes=b))

    per_dim = {}
    for d in DIMS:
        sel = [x for x in items if x['dimension'] == d]
        diffs = [x['difficulty'] for x in sel if x['difficulty'] is not None]
        discs = [x['discrimination'] for x in sel if x['discrimination'] is not None]
        per_dim[d] = dict(
            items=len(sel),
            mean_difficulty=round(sum(diffs) / len(diffs), 4) if diffs else None,
            mean_discrimination=round(sum(discs) / len(discs), 4) if discs else None,
            high_discrimination_items=sorted(x['sample_id'] for x in sel
                                             if x['discrimination'] is not None and x['discrimination'] >= HIGH_DISC),
            saturated_items=sorted(x['sample_id'] for x in sel if x['saturated']),
            floor_items=sorted(x['sample_id'] for x in sel if x['floor']),
            repeat_flip_items=sorted(x['sample_id'] for x in sel if x['any_repeat_flip']))
    high = sorted((x for x in items if x['discrimination'] is not None and x['discrimination'] >= HIGH_DISC),
                  key=lambda x: -x['discrimination'])
    report = dict(format='okb-item-stats-v2', run_id=args.run_id,
                  plan_sha256=plan_sha, results_sha256=sha(results_path),
                  methodology=dict(
                      difficulty='跨两 Agent assessed 任务通过率均值',
                      discrimination='|openclaw − hermes| assessed 通过率差，≥%.1f 记高区分' % HIGH_DISC,
                      conservative='unassessable 计入分母'),
                  summary=dict(items=len(items),
                               high_discrimination=len(high),
                               saturated=sum(1 for x in items if x['saturated']),
                               floor=sum(1 for x in items if x['floor']),
                               repeat_flip=sum(1 for x in items if x['any_repeat_flip'])),
                  dimensions=per_dim, items=items)
    (out_dir / 'item-stats-v2.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')

    lines = ['# item 难度/区分度台账（v2）', '',
             '- 口径：难度=两 Agent assessed 通过率均值；区分度=|Δ通过率|，≥%.1f 记高区分；波动=同一 Agent 三次重复结论不一致。' % HIGH_DISC,
             '- 绑定：plan sha `%s…` · results sha `%s…`（`score/judge-full-3-replay`）' % (plan_sha[:12], report['results_sha256'][:12]),
             '']
    lines.append('## 总览')
    lines.append('')
    lines.append('- 题目 %d · 高区分 %d · 饱和（天花板）%d · 地板 %d · 重复波动 %d'
                 % (report['summary']['items'], report['summary']['high_discrimination'],
                    report['summary']['saturated'], report['summary']['floor'], report['summary']['repeat_flip']))
    lines.append('')
    lines.append('## 分维度')
    lines.append('')
    lines.append('| 维度 | 题数 | 平均难度 | 平均区分度 | 高区分题 | 饱和题 | 地板题 | 波动题 |')
    lines.append('|---|---|---|---|---|---|---|---|')
    for d in DIMS:
        x = per_dim[d]
        lines.append('| %s | %d | %s | %s | %d | %d | %d | %d |' % (
            ZH[d], x['items'], x['mean_difficulty'], x['mean_discrimination'],
            len(x['high_discrimination_items']), len(x['saturated_items']),
            len(x['floor_items']), len(x['repeat_flip_items'])))
    lines.append('')
    lines.append('## 高区分题（|Δ|≥%.1f）' % HIGH_DISC)
    lines.append('')
    for x in high:
        lines.append('- `%s`（%s）Δ=%.2f（openclaw %.2f / hermes %.2f）'
                     % (x['sample_id'], ZH[x['dimension']], x['discrimination'],
                        x['openclaw']['pass_rate_assessed'], x['hermes']['pass_rate_assessed']))
    lines.append('')
    lines.append('## 饱和/地板/波动清单（供人工复核）')
    lines.append('')
    for d in DIMS:
        x = per_dim[d]
        if x['saturated_items'] or x['floor_items'] or x['repeat_flip_items']:
            lines.append('- %s：饱和 %s；地板 %s；波动 %s' % (
                ZH[d], x['saturated_items'] or '无', x['floor_items'] or '无', x['repeat_flip_items'] or '无'))
    (out_dir / 'item-stats-v2.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(dict(out=str(out_dir), items=len(items), high_discrimination=len(high),
                          saturated=report['summary']['saturated'], floor=report['summary']['floor'],
                          repeat_flip=report['summary']['repeat_flip']), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
