#!/usr/bin/env python3
"""okb · 判官 A/A 稳定性分析：同请求两轮（可选三轮多数投票）的批次翻转率。

输入：至少两份 records.jsonl（每行 protocol/model/request_sha256/response），
可选 requests.json（semantic-requests，用于按 task/check 归因），
可选 results-a/results-b（评分结果，用于任务级翻转对比）。
输出（--out-dir）：judge-aa-v2.json / judge-aa-v2.md；若给第三轮则另写
records-majority.jsonl（多数投票；三方互异记 unresolved 并回退 A 轮，台账如实记录）。

判定与口径说明：翻转只统计"同 request_sha256 的 result 不同"；rationale 差异不计。
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path


def load_records(path: Path):
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            if set(row) != {'protocol', 'model', 'request_sha256', 'response'}:
                raise ValueError('invalid record fields in %s' % path)
            out[row['request_sha256']] = row['response']['result']
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--records-a', required=True)
    ap.add_argument('--records-b', required=True)
    ap.add_argument('--records-c', default=None)
    ap.add_argument('--requests', default=None)
    ap.add_argument('--results-a', default=None)
    ap.add_argument('--results-b', default=None)
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args(argv)
    try:
        a, b = load_records(Path(args.records_a)), load_records(Path(args.records_b))
        c = load_records(Path(args.records_c)) if args.records_c else None
        out_dir = Path(args.out_dir).resolve()
        if set(a) != set(b) or (c is not None and set(c) != set(a)):
            raise ValueError('records 请求集合不一致（不是同批请求）')
        if not out_dir.is_dir():
            raise ValueError('out-dir must exist')
        meta = {}
        if args.requests:
            reqs = json.loads(Path(args.requests).read_text(encoding='utf-8'))
            meta = {r['request_sha256']: (r['task_id'], r['check_id']) for r in reqs}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('judge-aa rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    flips = [(k, a[k], b[k]) for k in sorted(a) if a[k] != b[k]]
    transitions = collections.Counter('%s->%s' % (x[1], x[2]) for x in flips)
    by_check = collections.Counter(meta[k][1] for k, _, _ in flips if k in meta)
    by_task = collections.Counter(meta[k][0] for k, _, _ in flips if k in meta)
    report = dict(format='okb-judge-aa-v2', rounds=2 + (1 if c else 0),
                  requests=len(a), flips=len(flips),
                  flip_rate=round(len(flips) / len(a), 4),
                  transitions=dict(transitions),
                  flips_by_check=dict(by_check.most_common()),
                  flips_by_task=dict(by_task.most_common()))

    if c is not None:
        majority, unresolved = {}, 0
        for k in a:
            votes = collections.Counter([a[k], b[k], c[k]])
            top, n = votes.most_common(1)[0]
            if n == 1:  # 三方互异
                unresolved += 1
                majority[k] = a[k]
            else:
                majority[k] = top
        report['majority'] = dict(unresolved=unresolved)
        with (out_dir / 'records-majority.jsonl').open('w', encoding='utf-8', newline='\n') as f:
            for line in Path(args.records_a).read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                row['response'] = dict(row['response'], result=majority[row['request_sha256']])
                f.write(json.dumps(row, ensure_ascii=False) + '\n')

    if args.results_a and args.results_b:
        ra = {t['task_id']: t for t in json.loads(Path(args.results_a).read_text(encoding='utf-8'))}
        rb = {t['task_id']: t for t in json.loads(Path(args.results_b).read_text(encoding='utf-8'))}
        task_diff = [k for k in ra if k in rb and (ra[k]['task_outcome'], ra[k].get('verdict')) !=
                     (rb[k]['task_outcome'], rb[k].get('verdict'))]
        report['tasks'] = len(ra)
        report['task_changes'] = len(task_diff)
        report['task_change_rate'] = round(len(task_diff) / len(ra), 4)
        report['task_change_examples'] = sorted(task_diff)[:20]

    (out_dir / 'judge-aa-v2.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    lines = ['# 判官 A/A 稳定性（同请求多轮）', '',
             '- 请求 %d · 翻转 %d（%.2f%%）· 轮数 %d' % (
                 report['requests'], report['flips'], 100 * report['flip_rate'], report['rounds']), '',
             '## 结果迁移（A→B）', '']
    for x, n in transitions.most_common():
        lines.append('- `%s`：%d' % (x, n))
    lines += ['', '## 翻转最多的检查（Top 15）', '']
    for cid, n in list(by_check.most_common(15)):
        lines.append('- `%s`：%d' % (cid, n))
    if 'task_changes' in report:
        lines += ['', '## 任务级', '',
                  '- 任务结论变化 %d / %d（%.2f%%）；示例：%s' % (
                      report['task_changes'], report['tasks'], 100 * report['task_change_rate'],
                      ', '.join(report['task_change_examples'][:8]))]
    if 'majority' in report:
        lines += ['', '## 三轮多数投票', '',
                  '- 三方互异（未解）%d 条，已回退 A 轮结果并如实记账' % report['majority']['unresolved']]
    (out_dir / 'judge-aa-v2.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('requests', 'flips', 'flip_rate') if k in report}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
