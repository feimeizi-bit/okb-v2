#!/usr/bin/env python3
"""okb · 交付样例包 b 生成器：从完成评分批次派生「任务 + 证据切片 + 评分解释」。

只读输入，不联网；输出目录必须全新（不覆盖）。每个案例一个 .md +
全部案例共用一个 evidence-slices.jsonl（被引用的原始证据行）。
隐私：证据在引擎侧已做 PII 占位符化；本包仍按私有评测材料保管。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

CASES = [
    ("openclaw-similar_wifi_001-r2", "相近区分 / 混淆（产物写错对象）"),
    ("hermes-boundary_pii_mask_001-r3", "边界识别 / 正确拒记（withhold）"),
    ("openclaw-update_delayed_001-r1", "动态更新 / 生效日前提前使用新址"),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_jsonl(path: Path):
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report', required=True)
    ap.add_argument('--results', required=True)
    ap.add_argument('--paper', required=True)
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding='utf-8'))
        results = {r['task_id']: r for r in json.loads(Path(args.results).read_text(encoding='utf-8'))}
        paper = {s['id']: s for s in load_jsonl(Path(args.paper))}
        out = Path(args.out_dir).resolve()
        if out.exists() or not out.parent.is_dir():
            raise ValueError('out-dir 必须全新且父目录存在')
        plan_sha = report.get('plan_sha256')
        if any(r.get('plan_sha256') != plan_sha for r in results.values()):
            raise ValueError('results/report 未绑定同一 plan')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('sample-pack rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    tasks = {t['task_id']: t for t in report['tasks']}
    slices, manifest = [], {}
    out.mkdir()
    (out / 'cases').mkdir()

    def write(name: str, text: str):
        p = out / name
        p.write_text(text, encoding='utf-8', newline='\n')
        manifest[str(p.relative_to(out))] = sha(p.read_bytes())

    for tid, title in CASES:
        task, res, sample = tasks[tid], results[tid], paper[tasks[tid]['sample_id']]
        agent = task['agent']
        lines = ['# 样例 %s · %s' % (tid, title), '',
                 '- 维度：`%s` · 结果：**%s / %s**（score=%s）· judged_by=规则+判官(gpt-6.1-sol)'
                 % (task['dimension'], task['task_outcome'], task['error_class'] or '—', task['score']), '']
        lines += ['## 任务', '',
                  '- 剧本（会话/相位/首轮原话，节选）：']
        for s in sample['script']:
            turn = s['turns'][0]['text']
            lines.append('  - `%s`（%s）：%s' % (s['session'], s.get('phase'), turn))
        if sample.get('fixture'):
            lines += ['- 初始文件：' + '；'.join('`%s`' % f['path'] for f in sample['fixture'])]
        lines += ['- 正确行为（expect）：%s' % json.dumps(sample.get('expect', {}).get('reference_answer'),
                                                        ensure_ascii=False), '']
        lines += ['## 评分解释（逐检查）', '']
        for c in task['checks']:
            lines.append('- `%s`（%s，on_failure=%s）→ **%s**' % (c['id'], c['layer'], c.get('on_failure'), c['result']))
            if c.get('rationale'):
                lines.append('  - 理由：%s' % c['rationale'])
            for cite in c.get('citations') or []:
                lines.append('  - 引用：`%s` %s → %s' % (cite['evidence_id'], cite['pointer'],
                                                         cite['quote'][:120].replace(chr(10), ' / ')))
        lines += ['', '## 关键证据切片', '']
        for cite in [c for chk in task['checks'] for c in (chk.get('citations') or [])]:
            slices.append(dict(task_id=tid, sample_id=task['sample_id'], kind='citation',
                               evidence_id=cite['evidence_id'], pointer=cite['pointer'], quote=cite['quote']))
            lines.append('- 引用 `%s` %s → `%s`' % (cite['evidence_id'], cite['pointer'],
                                                    cite['quote'][:200].replace(chr(10), ' / ')))
        lines += ['', '## 兜底证据行（原始证据 id → 载荷）', '']
        for e in task.get('evidence') or []:
            slices.append(dict(task_id=tid, sample_id=task['sample_id'], kind='backing_evidence',
                               evidence_id=e['id'], source=e['source'], type=e['type'], payload=e['payload']))
            lines.append('- `%s`（%s/%s）：%s' % (e['id'], e['source'], e['type'],
                                                  json.dumps(e['payload'], ensure_ascii=False)[:300]))
        lines += ['', '## 复现', '',
                  '```bash',
                  'python tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \\',
                  '  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \\',
                  '  --out-dir <新目录> --replay score/judge-full-3/records.jsonl --model gpt-6.1-sol \\',
                  '  --outcome-version v2 --evidence-policy scoped-v2',
                  '# 然后在 results.json 中按 task_id=%s 定位本行' % tid,
                  '```', '']
        write('cases/%s.md' % tid, '\n'.join(lines))

    with (out / 'evidence-slices.jsonl').open('w', encoding='utf-8', newline='\n') as f:
        for row in slices:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    manifest['evidence-slices.jsonl'] = sha((out / 'evidence-slices.jsonl').read_bytes())

    readme = ['# 样例包 b · 示例任务 / 真实证据 / 评分结果', '',
              '来源：`runs/v2r3_20261010-healed`（552 真实任务）+ `score/judge-full-3-replay`（判官 gpt-6.1-sol）。',
              'plan sha `%s…`；口径 scoped-v2 + 四层评分；案例均为真实运行行，未篡改。' % plan_sha[:12], '',
              '案例：'] + ['- `%s` —— %s' % c for c in CASES] + [
              '', '说明：',
              '- 证据在引擎采集时已完成 PII 占位符化；本包仍按私有评测材料保管。',
              '- 逐检查理由与引用来自判官回放记录；判断正确性与人工复核状态见 `score/final-report-r12/`。',
              '- 本包由 `tools/make_sample_pack.py` 确定性生成（同输入同字节）。', '']
    write('README.md', '\n'.join(readme))
    (out / 'manifest.json').write_text(json.dumps({'format': 'okb-sample-pack-v2-draft',
                                                   'plan_sha256': plan_sha, 'files': manifest},
                                                  ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(dict(out=str(out), cases=len(CASES), slices=len(slices)), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
