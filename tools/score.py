#!/usr/bin/env python3
"""okb · 规则评分器 CLI（CONTRACT §5/§7/§13）。

输入：runs/<run_id>/{evidence.jsonl, manifest.json, raw/} + paper.jsonl
输出：verdicts.jsonl（§13 行形状；A 建占位、B 写内容）
退出码：0 全部行合法 / 1 有行未过自检（拒绝写出）/ 2 输入不可读（对齐 validate.py 约定）
确定性：不读时钟、不读环境变量、不写绝对路径 —— 同输入逐字节同输出（replay 验收）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scoring import decide as scoring_decide          # noqa: E402
from scoring.index import EvidenceIndex, InputErrorLocal  # noqa: E402
from scoring.rows import check_row, verdict_row, write_verdicts  # noqa: E402
from loader import load_jsonl                          # noqa: E402


def score_run(run_dir, paper_rows, *, validate_evidence=False, sample_filter=None, judge=None):
    idx = EvidenceIndex.load(run_dir, validate_schema=validate_evidence)
    agents = [a["name"] for a in idx.manifest.get("agents", [])]
    if not agents:
        raise InputErrorLocal("manifest.agents 为空")
    m_sv = idx.manifest.get("suite_version")
    # 真实 task_id 全集（彩排抓到的缺陷：此前只合成无后缀 id，merge 后 -r1/-r2 行
    # 被静默丢弃——评分侧不得再按 f"{agent}-{sid}" 猜 id）
    all_tids = {r.get("task_id") for r in idx.rows if r.get("task_id")}

    def task_variants(agent: str, sid: str) -> list[str]:
        base = f"{agent}-{sid}"
        out = [base] if base in all_tids else []
        reps = []
        pre = base + "-r"
        for tid in all_tids:
            if tid.startswith(pre):
                suffix = tid[len(base):]
                if re.fullmatch(r"-r\d+", suffix):
                    reps.append(tid)
        out += sorted(reps, key=lambda x: int(x.rsplit("-r", 1)[1]))
        return out

    rows = []
    for sample in paper_rows:
        sv = (sample.get("meta") or {}).get("suite_version") or sample.get("suite_version")
        if m_sv and sv and str(m_sv) != str(sv):
            raise InputErrorLocal(f"paper suite_version {sv} ≠ manifest {m_sv}（拿 v0.2 的题判 v0.1 的证据？）")
        if sample_filter and sample["id"] not in sample_filter:
            continue  # R1：行数守护同步尊重本过滤（见下）
        for agent in agents:
            for task_id in task_variants(agent, sample["id"]):
                if not idx.task_rows(task_id):
                    continue  # 该 agent 没跑过这条样本：不产行（行数守护在 CLI 层核）
                ts = scoring_decide.decide_task(sample, idx, task_id, judge=judge)
                rows.append((ts, verdict_row(ts)))
    return idx, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="okb 规则评分器（§13）")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--out", default=None, help="默认 <run-dir>/verdicts.jsonl")
    ap.add_argument("--sample-id", action="append", default=None)
    ap.add_argument("--validate-evidence", action="store_true")
    ap.add_argument("--judge", default=None, help="live=调用第二族模型裁软锚点（默认 NullJudge）")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--stdout-only", action="store_true")
    args = ap.parse_args(argv)

    # R6（momus 结转）：--judge 只认 live；未实现值**拒绝**而非静默退回 NullJudge
    # （静默假 live 会污染 P4-4 判官台账）。shadow 三方对照由 P4-4 流程
    # （annotate/judge_drift + verdicts.judged.jsonl）承担，不在单跑器里半吊子。
    if args.judge not in (None, "live"):
        print(f"--judge={args.judge!r} 暂不支持：本工具仅 live（shadow 请走 P4-4 三对照流程）",
              file=sys.stderr)
        return 2
    judge = None
    if args.judge == "live":
        from scoring.judge_client import OpenAICompatJudge
        judge = OpenAICompatJudge(ROOT, model=args.judge_model,
                                  cache_dir=os.path.abspath(args.run_dir))

    try:
        paper = load_jsonl(args.paper)
        idx, rows = score_run(args.run_dir, paper,
                              validate_evidence=args.validate_evidence,
                              sample_filter=set(args.sample_id or []) or None, judge=judge)
    except InputErrorLocal as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2

    # P1-2：行数守护（注释既诺「CLI 层核」，此处落地）——评分行数必须等于
    # manifest.tasks 数；差集打印前 5 个 task_id。T0 分片漏并/后缀吞行当场拦截。
    _sf = set(args.sample_id or [])
    all_tasks = [t for t in (idx.manifest.get("tasks") or [])]
    if _sf:
        all_tasks = [t for t in all_tasks if t.get("sample_id") in _sf]
    expected_tasks = [t.get("task_id") for t in all_tasks]
    if expected_tasks:
        from collections import Counter
        got_c = Counter(r["task_id"] for _, r in rows)
        exp_c = Counter(expected_tasks)
        missing = sorted((exp_c - got_c).elements())
        extra = sorted((got_c - exp_c).elements())
        if missing or extra:
            print(f"行数守护失败：manifest.tasks={len(expected_tasks)} · scored={len(rows)}", file=sys.stderr)
            if missing:
                print("  缺(前5): " + ", ".join(missing[:5]), file=sys.stderr)
            if extra:
                print("  多(前5): " + ", ".join(extra[:5]), file=sys.stderr)
            return 2

    problems = []
    for row in (r for _, r in rows):
        for p in check_row(row, idx):
            problems.append(f"{row['task_id']}: {p}")
    if problems:
        print("verdicts 行自检未通过，拒绝写出：", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1

    if args.stdout_only:
        for _, r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    out = args.out or os.path.join(args.run_dir, "verdicts.jsonl")
    write_verdicts(out, [r for _, r in rows])
    by = {}
    for r in (row for _, row in rows):
        by[r["verdict"] or r["status"]] = by.get(r["verdict"] or r["status"], 0) + 1
    print(f"✅ 写出 {out}：{len(rows)} 行 · 分布 {by}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
