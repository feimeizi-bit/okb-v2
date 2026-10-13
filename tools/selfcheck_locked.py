#!/usr/bin/env python3
"""okb · 锁定自检（F-7 纪律的工具化；v1.0 冻结硬门，batchA 首次实练）。

用途：证明「一份 suite 文件 + 五档策略」在**当前机器**上可从零跑绿，且读数与
`--expect` 期望矩阵 0 diff。锁提交必附本脚本 fresh 全绿（见 notes/p5-preregistration-v1.0.md §4）。

    python3 tools/selfcheck_locked.py --suite samples/v1.0-batchA.yaml \
        --policy-prefix samples/mock-policies/batchA- --policy-suffix v0.3 \
        --expect notes/v0.3-batchA-expect-cells.json [--write]

--write  用本次实测矩阵生成/覆写 expect 文件（**仅限设计冻结前**；冻结后禁用）。
逐格比较 pass 与 verdict；reason 摘要仅供诊断（含证据 ID，不作为语义锁）。
临时目录重建不等于 fresh clone；--fresh / --public 尚未实现，不宣称支持。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
TIERS = ["remember", "coexist", "replace", "forget", "gamed"]
VERDICTS = {"remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"}


def matrix_diffs(exp, matrix, ids, sha):
    """Fail closed on missing/extra cells and on same-pass/different-class drift."""
    if not isinstance(exp, dict):
        return ["expect 必须是对象"]
    diffs = []
    if exp.get("paper_sha256") != sha:
        diffs.append("paper sha 漂移")
    if exp.get("tiers") != TIERS:
        diffs.append("tiers 声明与五档顺序不一致")
    cells = exp.get("cells")
    if not isinstance(cells, dict):
        return diffs + ["expect.cells 必须是对象"]
    if set(cells) != set(ids):
        diffs.append("期望样本集合不一致（缺失或多余样本）")
    if set(matrix) != set(ids):
        diffs.append("实测样本集合不一致（缺失或多余样本）")
    for sid in ids:
        wants, gots = cells.get(sid), matrix.get(sid)
        if not isinstance(wants, dict) or set(wants) != set(TIERS):
            diffs.append(f"{sid}: 期望五档集合不完整或有多余档")
        if not isinstance(gots, dict) or set(gots) != set(TIERS):
            diffs.append(f"{sid}: 实测五档集合不完整或有多余档")
        for tier in TIERS:
            want = wants.get(tier) if isinstance(wants, dict) else None
            got = gots.get(tier) if isinstance(gots, dict) else None
            for name, cell in (("期望", want), ("实测", got)):
                if not isinstance(cell, dict) or type(cell.get("pass")) is not bool or "verdict" not in cell:
                    diffs.append(f"{sid}/{tier}: {name} 格必须含 bool pass 和 verdict")
                    continue
                verdict = cell["verdict"]
                if (verdict is not None and (not isinstance(verdict, str) or verdict not in VERDICTS)) or cell["pass"] != (verdict == "remembered"):
                    diffs.append(f"{sid}/{tier}: {name} 格 verdict 非法或与 pass 矛盾")
            if isinstance(want, dict) and isinstance(got, dict):
                if any(got.get(k) != want.get(k) for k in ("pass", "verdict")):
                    diffs.append(f"{sid}/{tier}: pass/verdict 漂移（期望 {want.get('verdict')}, 实得 {got.get('verdict')}）")
    return diffs


def sh(args, tag):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        print(f"FAIL {tag}: rc={r.returncode}\n{(r.stdout or '')[-500:]}\n{(r.stderr or '')[-500:]}")
        sys.exit(2)
    return r.stdout or ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True)
    ap.add_argument("--policy-prefix", required=True)
    ap.add_argument("--policy-suffix", required=True)
    ap.add_argument("--expect", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="okb-selfcheck-") as td:
        paper = str(Path(td) / "paper.jsonl")
        sh([PY, "tools/build_paper.py", a.suite, "-o", paper], "build")
        sha = hashlib.sha256(Path(paper).read_bytes()).hexdigest()
        out = sh([PY, "tools/validate.py", paper], "lint")
        assert "0 error" in out, f"lint 非零错误: {out[-200:]}"
        import yaml
        ids = [s["id"] for s in yaml.safe_load((ROOT / a.suite).read_text(encoding="utf-8"))["samples"]]
        if not ids or len(ids) != len(set(ids)):
            print("FAIL: suite 必须含非空且唯一的样本 ID")
            return 1
        matrix, row_problems = {}, []
        for t in TIERS:
            pol = f"{a.policy_prefix}{t}.{a.policy_suffix}.yaml"
            rd = str(Path(td) / "runs" / t)
            sh([PY, "tools/simulate.py", "--paper", paper, "--policy", pol,
                "--run-id", f"sc-{t}", "--runs-dir", rd], f"simulate:{t}")
            run = next(Path(rd).rglob(f"sc-{t}"))
            sh([PY, "tools/score.py", "--run-dir", str(run), "--paper", paper], f"score:{t}")
            seen = set()
            for ln in (run / "verdicts.jsonl").read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                sid = r.get("sample_id")
                if sid not in ids or sid in seen:
                    row_problems.append(f"{t}/{sid}: 多余或重复的评分行")
                    continue
                seen.add(sid)
                verdict = r.get("verdict")
                valid = ((r.get("status") == "ok" and isinstance(verdict, str) and verdict in VERDICTS)
                         or (r.get("status") == "skip" and verdict is None))
                if not valid or r.get("judge_error"):
                    row_problems.append(f"{t}/{sid}: 环境/判官失败或不合法评分行，不计为弱档失败")
                matrix.setdefault(sid, {})[t] = {
                    "pass": verdict == "remembered", "verdict": verdict,
                    "violated_hard_first": str(r.get("reason", "")).split("|")[0].strip()[:60]}
            if seen != set(ids):
                row_problems.append(f"{t}: 缺少样本评分行 {sorted(set(ids) - seen)}")
        for sid in ids:
            gamed = matrix.get(sid, {}).get("gamed", {})
            if gamed.get("verdict") not in (VERDICTS - {"remembered"}):
                row_problems.append(f"{sid}/gamed: 必须有实际失败类别，pass/skip/缺行都不是被识破")
        if row_problems:
            for problem in row_problems[:10]:
                print("  FAIL", problem)
            return 1
        exp_file = ROOT / a.expect
        if a.write:
            exp_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = exp_file.with_suffix(".json.t")
            tmp.write_text(json.dumps({"paper_sha256": sha, "tiers": TIERS, "cells": matrix},
                                      ensure_ascii=False, indent=1, sort_keys=True),
                                         encoding="utf-8", newline="\n")
            import os
            os.replace(tmp, exp_file)
            print(f"✅ expect 矩阵已生成：{a.expect}（{len(matrix)} 样本 × {len(TIERS)} 档，paper sha={sha[:16]}…）")
            return 0
        if not exp_file.is_file():
            print(f"FAIL: 期望矩阵缺失 {a.expect}（设计冻结前用 --write 生成，冻结后禁改）")
            return 1
        try:
            exp = json.loads(exp_file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"FAIL: 无法读取期望矩阵: {exc}")
            return 1
        diffs = matrix_diffs(exp, matrix, ids, sha)
        rem_pass = sum(1 for sid in ids if matrix.get(sid, {}).get("remember", {}).get("pass"))
        n = max(1, len(ids))
        weak_rates = []
        for wt in ("coexist", "replace", "forget", "gamed"):
            weak_rates.append(sum(1 for sid in ids if matrix.get(sid, {}).get(wt, {}).get("pass")) / n)
        gap = rem_pass / n - (sum(weak_rates) / len(weak_rates) if weak_rates else 0)
        full_suite = len(ids) >= 50   # 预注册 gap 门槛适用于全量 suite；draft 批次只报数
        print(f"档位矩阵：T1 {rem_pass}/{n} ｜ 弱档均值 {[f'{x:.2f}' for x in weak_rates]} ｜ T1-T2 gap={gap:.2f}"
              + ("（预注册门槛 ≥0.6，全量适用）" if full_suite else "（draft 批次：只报不拦）"))
        if diffs or (full_suite and gap < 0.6):
            for d in diffs[:10]:
                print("  DIFF", d)
            print("❌ 锁定自检未过")
            return 1
        print("✅ 锁定自检全绿（临时重建 → lint 0 错 → 五档 pass/verdict 0 diff → gamed 实际失败 → gap 适用门通过）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
