#!/usr/bin/env python3
"""okb · C-2 对拍（roadmap P4-3）：harness（stdio mock adapter）↔ 离线模拟器。

同一策略文件、同一 paper、同一 runner（run_batch）下，两条链路各跑一遍→规则评分，
verdicts 归一化后必须**逐字节一致**。任何一格分歧 = 模拟器结论（含 C-1 区分度矩阵）
不可外推到真实 harness，C-2 不得升「已取得」。

归一化（只剥「链路无关的易变字段」，语义字段一个不动）：
  - evidence_refs / 路径中的 runs 根前缀与临时目录名
  - *_at / taken_at / timestamp 类时间字段
  - manifest 相关（不参与比对；只比 verdicts.jsonl 行）
  - evidence 全局 seq（ev_<role>_<seq> 的 seq 打桩为 <SEQ>）。修订记录：首跑（保真修复
    前）确有 seq 整体偏移；momus 复核实证修复后两链行数/seq 全等（354/354），打桩降为
    保险余量。「角色前缀携带跨链信息」的说法收回——前缀=run 短号派生，两侧尾段同名时
    恒相等（空判据 P2-2）；真判据 = hit 计数 + 绝对下限 + 事件流多重集。
- **绝对下限（momus P0#2：双侧同错时「互等」不构成证据）**：① 每链 env_error ≤2/20、
  ok∧verdict ≥75%；② 敏感值明文探针（防匿名化双侧同坏）；③ evidence 流按归一化行
  多重集对比（verdict 层等而事件层不等 = 就是分歧）。
  ⚠ 99% 栈共享（run_batch/collectors/score 同体），故 C-2 语义=「两考生实现等价」，
  非「评测器正确性」——后者由变异测试/负向集/真实批 κ 背书，claims 措辞照此收敛。

用法：python tools/c2_parity.py [--tiers remember,coexist,replace,forget,gamed]
产物：runs/parity/<tier>/diff-*.json（有差异时）+ stdout 汇总；exit 0 = 全档一致。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
# P1-1 教训：子串匹配把 status/hard_evaluated/hard_violated 也吞了（15/18 实比却声称
# 「语义字段不减」）。易变键一律**精确名单**，新字段默认进比对。
VOL_KEYS = {"ts", "time", "timestamp", "duration_ms", "elapsed",
            "started_at", "finished_at", "taken_at", "written_at", "generated_at"}


def norm(obj):
    """递归归一化：精确易变键名单置 <VOL>；字符串里的绝对路径/运行目录名打桩。"""
    if isinstance(obj, dict):
        return {k: ("<VOL>" if str(k).lower() in VOL_KEYS else norm(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [norm(x) for x in obj]
    if isinstance(obj, str):
        s = re.sub(str(ROOT).replace("\\", "/") + r"[^\s\"']*", "<ROOT>", obj.replace("\\", "/"))
        s = re.sub(r"runs[/\\](parity|parity-smoke|smoke)[^\"'\s]*", "<RUNS>", s)
        s = re.sub(r"(par-[hsm]-[A-Za-z0-9]+|[a-z]+_smoke[a-z0-9_-]*)", "<RUNID>", s)
        s = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.+]+", "<TS>", s)
        s = re.sub(r"\d[\d,]*(\.\d+)?\s*MB", "<MB>", s)  # preflight 磁盘读数：环境噪声非链差异
        # evidence 全局 seq 因 harness 多 emit reset/健康事件而整体后移：
        # 打桩 seq、保留角色前缀（ev_<role>_<seq>）——前缀相等仍被比对，语义不减。
        s = re.sub(r"(ev_[a-z0-9]+_)\d+", r"\1<SEQ>", s)
        return s
    return obj


def sh(cmd, tag):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        print(f"❌ {tag} 失败 rc={r.returncode}\n{(r.stdout or '')[-800:]}\n{(r.stderr or '')[-800:]}")
        sys.exit(2)
    return r


def load_verdicts(d: Path) -> list[dict]:
    p = d / "verdicts.jsonl"
    assert p.is_file(), f"缺 {p}（先 score）"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return sorted(rows, key=lambda r: (r.get("sample_id", ""), str(r.get("task_id", ""))))


def find_run_dir(root: Path, run_id: str) -> Path:
    for cand in root.rglob(run_id):
        if (cand / "evidence.jsonl").is_file():
            return cand
    hits = [p.parent for p in root.rglob("evidence.jsonl") if run_id in str(p)]
    assert hits, f"找不到 {run_id} 的产物于 {root}"
    return hits[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default="remember,coexist,replace,forget,gamed")
    ap.add_argument("--paper", default="build/paper-v0.2.jsonl")
    a = ap.parse_args(argv)
    paper = ROOT / a.paper
    assert paper.is_file(), f"先 make-paper：{paper}"
    out_root = ROOT / "runs" / "parity"
    out_root.mkdir(parents=True, exist_ok=True)
    hist = out_root / "history"
    hist.mkdir(exist_ok=True)
    for old in out_root.glob("diff-*.json"):            # 首跑证据归档，不新旧混堆
        (hist / (old.stem + ".prerev.json")).write_bytes(old.read_bytes())
        old.unlink()
    results = {}
    for tier in [t.strip() for t in a.tiers.split(",") if t.strip()]:
        pol = ROOT / "samples" / "mock-policies" / f"{tier}.v0.2.yaml"
        assert pol.is_file(), pol
        hid, sid = f"par-h-{tier}", f"par-s-{tier}"
        hd = out_root / "harness" / hid
        sd = out_root / "sim" / sid
        if hd.exists():
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(hd)], check=False)
        if sd.exists():
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(sd)], check=False)
        # 1) harness：engine runner + stdio MockAdapter（真子进程）
        sh([PY, "-m", "engine.runner", "--agent", "mock", "--mock-policy", str(pol),
            "--paper", str(paper), "--run-id", hid, "--runs-dir", str(out_root / "harness")],
           f"harness:{tier}")
        # 2) 模拟器：in-process PolicyAdapter（C-1 矩阵用的那条链）
        sh([PY, str(ROOT / "tools" / "simulate.py"), "--paper", str(paper),
            "--policy", str(pol), "--run-id", sid, "--runs-dir", str(out_root / "sim")],
           f"sim:{tier}")
        hrun, srun = find_run_dir(out_root / "harness", hid), find_run_dir(out_root / "sim", sid)
        sh([PY, str(ROOT / "tools" / "score.py"), "--run-dir", str(hrun), "--paper", str(paper)],
           f"score-h:{tier}")
        sh([PY, str(ROOT / "tools" / "score.py"), "--run-dir", str(srun), "--paper", str(paper)],
           f"score-s:{tier}")
        hv, sv = load_verdicts(hrun), load_verdicts(srun)
        # 期望行数从 manifest 推（P2-6：不硬编码 20）
        exp_h = len(json.loads((hrun / "manifest.json").read_text(encoding="utf-8"))["tasks"])
        exp_s = len(json.loads((srun / "manifest.json").read_text(encoding="utf-8"))["tasks"])
        assert len(hv) == exp_h and len(sv) == exp_s, f"{tier}: scored/manifest 不齐 {len(hv)}/{exp_h}·{len(sv)}/{exp_s}"
        for name, rows in (("harness", hv), ("sim", sv)):
            n = len(rows)
            okish = sum(1 for r in rows if r.get("status") == "ok" and r.get("verdict"))
            nonenv = sum(1 for r in rows if r.get("status") != "env_error")
            assert nonenv >= n - 2, f"{tier}/{name}: env_error {n - nonenv}/{n} 超阈——双侧同错嫌疑"
            assert okish >= int(n * 0.75), f"{tier}/{name}: 有效判定 {okish}/{n} <75%，退化"
        # PII 明文探针（M2 型双侧同坏）
        # R3：探针取 pii 节**声明值本身**（含 email/地址等非数字型），并扫 evidence+raw
        probe_vals = set()
        for line in paper.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            for item in (rec.get("pii") or []):
                # 只有声明值本体该被匿名化；id/kind 是合法标签（PII_PHONE_1 可出现在证据）
                v_ = str((item.get("value") if isinstance(item, dict) else item) or "")
                if len(v_) >= 5 and not v_.startswith("@"):
                    probe_vals.add(v_)
        if not probe_vals:
            print(f"  ⚠ {tier}: PII 探针无可用声明值（静默不检=盲区，需查 paper.pii 形状）")
        for tag, d in (("harness", hrun), ("sim", srun)):
            texts = [(d / "evidence.jsonl").read_text(encoding="utf-8")]
            for rf in list((d / "raw").rglob("*.jsonl")) + list((d / "raw").rglob("*.txt")):
                texts.append(rf.read_text(encoding="utf-8"))
            blob = "\n".join(texts)
            leak = [v_ for v_ in probe_vals if v_ in blob]
            assert not leak, f"{tier}/{tag}: PII 明文入证据/raw {leak[:2]}——匿名化双侧同坏"
        diffs = []
        for rh, rs in zip(hv, sv):
            nh, ns = norm(rh), norm(rs)
            if nh != ns:
                keys = sorted(set(nh) | set(ns))
                bad = [k for k in keys if nh.get(k) != ns.get(k)]
                diffs.append({"sample_id": rh.get("sample_id"), "fields": bad,
                              "harness": {k: nh.get(k) for k in bad},
                              "sim": {k: ns.get(k) for k in bad}})
        # 事件流取证 v2（momus R2：全局 Counter 会吞「task 内相对序」分歧——
        # 改按 task_id 归键的**有序列表**，序/归属分歧即现形）
        def stream_sig(d):
            sig = {}
            for ln in (d / "evidence.jsonl").read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                tid = str(r.get("task_id"))
                r2 = {k: v for k, v in r.items() if str(k).lower() not in {"id", "seq", "ts", "at"}}
                k_ = re.sub(r"(ev_[a-z0-9]+_)\d+", r"\1<SEQ>",
                            json.dumps(norm(r2), ensure_ascii=False, sort_keys=True))
                sig.setdefault(tid, []).append(k_)
            return sig
        sh_, ss_ = stream_sig(hrun), stream_sig(srun)
        if sh_ != ss_:
            tasks_bad = [k_ for k_ in sorted(set(sh_) | set(ss_)) if sh_.get(k_) != ss_.get(k_)]
            detail = {}
            for k_ in tasks_bad[:3]:
                a_, b_ = sh_.get(k_, []), ss_.get(k_, [])
                firstdiff = next((i for i, (x, y) in enumerate(zip(a_, b_)) if x != y), min(len(a_), len(b_)))
                detail[k_] = f"@{firstdiff}: H={ (a_[firstdiff] if firstdiff<len(a_) else 'EOF')[:120] }" \
                             f" S={ (b_[firstdiff] if firstdiff<len(b_) else 'EOF')[:120] }"
            diffs.append({"sample_id": "<evidence-stream>", "fields": ["ordered-per-task"],
                          "harness": {k_: len(sh_.get(k_, [])) for k_ in tasks_bad[:5]},
                          "sim": detail})
        verdict = "一致" if not diffs else f"分歧 {len(diffs)} 项"
        results[tier] = (len(hv), diffs)
        if diffs:
            (out_root / f"diff-{tier}.json").write_text(
                json.dumps(diffs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{tier}] {len(hv)} 行 verdicts+事件流对比 → {verdict}"
              + (f"（明细 runs/parity/diff-{tier}.json）" if diffs else ""))
    allok = all(not d for _, d in results.values())
    print("\nC-2 判据（v2）：verdicts 逐字段 + 绝对下限 + PII 探针(含raw/txt) + 事件流按task有序比对全过 → "
          + ("**一致（含退化防线）**" if allok else "**分歧，见 diff-*.json**"))
    print("注：C-2 语义=两考生实现等价（99% 栈共享）；评测器正确性另由变异测试/负向集/真实批 κ 背书")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
