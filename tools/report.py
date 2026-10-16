#!/usr/bin/env python3
"""okb · 报告层 v4.1（P3-1/2/3）：verdicts.jsonl → 多维成绩单（CONTRACT §7/§11 口径）。

v2 收口三轮验收全部指实项（P3 review BLOCK / momus M1~M7）：
- 可计分 = status=="ok" ∧ verdict∈五分类 ∧ judge_error 空（§7:245/§13:341）——v1 只看了 verdict
- 行级契约校验归报告层（枚举越域/状态↔verdict 矛盾/重复键/空文件全部拒报，exit≠0）
- 维度×Agent 双轴（§7:232 口径1）；task_id 兼容 repeat 后缀 `-rN`
- 覆盖率分母 = paper 该维样本数（不是行数的投影）；剔除公示带归因
- --sensitivity 三档真实重算（含 guard#5=off 对 low_conf/总分的完整影响）
- md 表格列数对齐（markdown-it 可渲染）；trace 印「引用数/该 agent 总引用数」
退出码：0 出报；1 行级契约/完整性问题或断链（报告器必须能失败）。
用法：python tools/report.py --runs runs/a,runs/b --paper build/paper-v0.2.jsonl \
        --out build/report [--sensitivity] [--trace 2] [--no-judged]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, "tools"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from loader import load_jsonl  # noqa: E402

DIMS = ["long_term_retention", "memory_recall", "dynamic_update",
        "similar_discrimination", "boundary_recognition", "task_reuse"]
VERDICTS = ["remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"]
NON_OK = {"timeout", "agent_error", "env_error", "skip"}


def parse_task(tid, sid):
    """task_id → (agent, repeat)。兼容 engine 的 `{agent}-{sample}[-rN]` 约定；
    无 task_id 的行归入单组 '-'（review P2-1：不得退化成一堆伪缺行）。"""
    if not tid:
        return "-", ""
    base = tid or sid
    rep = ""
    core = base
    if sid and sid != base:
        # 整体剥 `^(agent)-{sid}(-rN)?$`：一次到位，兼容 sid 自带 -rN（N2 怪例）
        m = re.match(r"^(.*)-" + re.escape(sid) + r"(-r\d+)?$", base)
        if m:
            core, rep = m.group(1), (m.group(2) or "")
    return (core or "default"), rep


def scorable(r, guard_off=False):
    v = r.get("verdict")
    st = r.get("status")
    je = r.get("judge_error")
    if v is None and st == "skip" and guard_off and "guard#5" in (r.get("reason") or ""):
        return True, "remembered"
    if st != "ok" or je or v not in VERDICTS:
        return False, v
    return True, v


def row_contract_problems(rows):
    """行级契约（归报告层执行——三评审指实此前无人管 verdicts 行）。返回 [(row, msg)]。"""
    out = []
    seen = {}
    for k, r in rows.items():
        if k in seen:
            out.append((k, "重复 task 键"))
        seen[k] = 1
        v, st, je = r.get("verdict"), r.get("status"), r.get("judge_error")
        if v is not None and v not in VERDICTS:
            out.append((k, f"verdict 越域 {v!r}"))
        if st not in {"ok", *NON_OK}:
            out.append((k, f"status 非法 {st!r}"))
        if st != "ok" and v is not None:
            out.append((k, "§13#5 违反：status≠ok 却带 verdict"))
        if st == "ok" and v is None and not je:
            out.append((k, "§13#5 违反：status=ok∧verdict=null∧无 judge_error"))
        if je and v is not None:
            out.append((k, "§13#5②违反：judge_error 非空却带 verdict"))
        if st in {"skip", "env_error"} and not (r.get("reason") and r.get("evidence_refs")):
            out.append((k, f"§7口径2/§13#6 违反：{st} 无 reason/证据"))  # timeout/agent_error 裸行只剔分母，不拒整报（review#3）
    return out


def load_run(d):
    vp = os.path.join(d, "verdicts.jsonl")
    if not os.path.exists(vp):
        raise SystemExit(f"缺 verdicts.jsonl：{vp}")
    rows = {}
    dups = []
    for line in open(vp, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            k = r.get("task_id") or r["sample_id"]
            if k in rows:
                dups.append(k)  # 重复键是静默 last-wins 的入口，必须在装载层拦截
            rows[k] = r
    if not rows:
        return None, None, None, "空 verdicts.jsonl"
    if dups:
        return None, None, None, f"重复 task 键：{dups[:3]}…"
    jp = os.path.join(d, "verdicts.judged.jsonl")
    judged = {}
    if os.path.exists(jp):
        for line in open(jp, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                judged[r.get("task_id") or r["sample_id"]] = r
    man = {}
    mp = os.path.join(d, "manifest.json")
    if os.path.exists(mp):
        man = json.load(open(mp, encoding="utf-8"))
    return rows, (judged or None), man, None


def stats(rows_, dim_total, guard_off=False):
    """§7:246 口径3（v4 二阶段）：每样本先取轮内均值，再跨样本等权；行只作诊断（n_rows）。
    coverage/低置信按可测样本数（§7:232）。"""
    per = {}
    excluded = []
    part = {}
    n_rows_by = {}
    n_rows = 0
    for r in rows_:
        ok_, v = scorable(r, guard_off)
        if ok_:
            per.setdefault(r["sample_id"], []).append(v)
            n_rows_by[r["sample_id"]] = n_rows_by.get(r["sample_id"], 0) + 1
            n_rows += 1
        else:
            why = r.get("status") if r.get("status") != "ok" else (
                "judge_error" if r.get("judge_error") else "越域/空")
            # G1 披露：skip 子类见光（探针未证实 ≠ 正常「缺采集不得给分」，两者都要点名）
            if why == "skip" and "probe_unverified" in str(r.get("reason") or "") + str(r.get("skipped_anchors") or ""):
                why = "skip·记忆探针未证实(ISS-13)"
            part.setdefault(r["sample_id"], []).append(why)
    n = len(per)
    for sid_, whys in sorted(part.items()):        # 部分轮剔除可见（review#1/momus N11b）
        keep = n_rows_by.get(sid_, 0)
        tag = ",".join(sorted(set(whys)))
        excluded.append(f"{sid_}({tag})" if not keep else f"{sid_}({tag};保留{keep}轮)")

    def mean_of(pred):
        vals = [sum(1 for v in per[s] if pred(v)) / len(per[s]) for s in per]
        return (sum(vals) / len(vals)) if vals else None
    cov = (n / dim_total) if dim_total else None
    return {"n_scored": n, "n_rows": n_rows, "n_total": dim_total,
            "acc": mean_of(lambda v: v == "remembered"),
            "wp_rate": mean_of(lambda v: v == "wrongly_persisted"),
            "wr_rate": mean_of(lambda v: v == "wrongly_reused"),
            "coverage": cov, "excluded": excluded}


def build_report(run_dirs, paper_rows, *, thr_n=10, thr_cov=0.8, sensitivity=False,
                 trace=0, judged_view=True, guard_off=False):
    paper = {s["id"]: s for s in paper_rows}
    dim_of = {sid: s["dimension"] for sid, s in paper.items()}
    dim_sizes = {d: sum(1 for s in paper.values() if s["dimension"] == d) for d in DIMS}
    rep = {"generated_by": "tools/report.py v4.1", "suite": None, "contract_version": None,
           "thresholds": {"n": thr_n, "cov": thr_cov}, "runs": []}
    problems = []
    for d in run_dirs:
        rows, judged, man, err = load_run(d)
        if err:
            problems.append(f"{d}: {err}")
            continue
        if man:
            rep["suite"] = rep["suite"] or man.get("suite_version")
            rep["contract_version"] = rep["contract_version"] or man.get("contract_version")
        for cp in row_contract_problems(rows):
            problems.append(f"{d}: {cp[0]} :: {cp[1]}")
        if judged_view and judged:
            for cp in row_contract_problems(judged):
                problems.append(f"{d}(judged): {cp[0]} :: {cp[1]}")
        # 完整性：每 agent 的行集合须覆盖 paper 全部样本
        by_agent = {}
        for k, r in rows.items():
            ag, _ = parse_task(r.get("task_id"), r["sample_id"])
            # N1（复审指实）：repeat 轮次行必须全保留——按 task 键收行，sample 集合另算
            by_agent.setdefault(ag, {})[k] = r
        for ag, got in by_agent.items():
            sids = {r["sample_id"] for r in got.values()}
            miss, ext = set(paper) - sids, sids - set(paper)
            if miss or ext:
                problems.append(f"{d}/{ag}: 缺行 {sorted(miss)[:5]} 多行 {sorted(ext)[:5]}")
        for ag, got in sorted(by_agent.items()):
            run_base = os.path.basename(d.rstrip("/\\"))
            entry = {"label": f"{run_base}/{ag}", "dir": d, "agent": ag,
                     "dims": {}, "overall": {}}
            for dim in DIMS:
                lst = [r for _k, r in sorted(got.items()) if dim_of.get(r["sample_id"]) == dim]
                s = stats(lst, dim_sizes[dim], guard_off)
                s["low_confidence"] = s["n_scored"] < thr_n or (s["coverage"] or 0) < thr_cov
                ws = [r for r in lst
                      if (paper.get(r["sample_id"], {}).get("expect") or {}).get("boundary_kind") == "withhold"]
                wper = {}
                for r in ws:
                    ok_, v = scorable(r, guard_off)
                    if ok_:
                        wper.setdefault(r["sample_id"], []).append(v)
                wvals = [sum(1 for v in wper[s] if v == "wrongly_persisted") / len(wper[s]) for s in wper]
                s["withhold"] = {"n_withhold": len({r["sample_id"] for r in ws}),
                                 "n_scored": len(wper),
                                 "wp_rate_within_withhold": (sum(wvals) / len(wvals)) if wvals else None}
                if judged_view and judged:
                    jr = [v for _k, v in sorted(judged.items())
                          if parse_task(v.get("task_id"), v["sample_id"])[0] == ag
                          and dim_of.get(v["sample_id"]) == dim]
                    js = stats(jr, dim_sizes[dim], guard_off)
                    s["judged"] = {"acc": js["acc"], "n_scored": js["n_scored"]}
                entry["dims"][dim] = s
            usable = [x for x in DIMS if not entry["dims"][x]["low_confidence"]
                      and entry["dims"][x]["acc"] is not None]
            entry["overall"]["usable_dims"] = usable
            entry["overall"]["overall_score"] = (
                sum(entry["dims"][x]["acc"] for x in usable) / len(usable)) if usable else None
            if not usable:
                entry["overall"]["note"] = (f"全部维度低置信（阈值 n≥{thr_n}∧cov≥{thr_cov:.0%}）→ 只出雷达，"
                                            "总分不予报告（§7 口径3；mock/pilot 体量下阈值仅为演练，见 §7:236 作用域）")
            if trace:
                entry["trace"] = trace_check(d, rows, ag, per_dim=trace, dim_of=dim_of)
            rep["runs"].append(entry)
    if sensitivity:
        arms = {}
        for label, kw in (("主口径（当前阈值）", {}),
                          ("thr0（不设门槛=§7:236 mock 适用档）", {"thr_n": 0, "thr_cov": 0.0}),
                          ("guard#5=off（完整重算）", {"guard_off": True})):
            sub, _p = build_report(run_dirs, paper_rows, trace=0, judged_view=False,
                                   thr_n=kw.get("thr_n", thr_n), thr_cov=kw.get("thr_cov", thr_cov),
                                   guard_off=kw.get("guard_off", False))
            arms[label] = [{"run": r["label"], "overall": r["overall"]["overall_score"],
                            "usable_dims": len(r["overall"]["usable_dims"])} for r in sub["runs"]]
        rep["sensitivity"] = arms
    return rep, problems


def trace_check(run_dir, rows, agent, per_dim=2, dim_of=None):
    """P3-3：该 agent 每维抽 N 行，核验其全部 evidence_refs 可定位（计数=引用数）。"""
    res = {"checked": 0, "total_refs": 0, "broken": []}
    ev_p = os.path.join(run_dir, "evidence.jsonl")
    ids, raws = {}, {}
    if os.path.exists(ev_p):
        for line in open(ev_p, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                ids[r["id"]] = True
                raws[r["id"]] = (r.get("raw_ref") or {}).get("path")
    dim_of = dim_of or {}
    by_dim = {}
    for k, r in rows.items():
        ag, _ = parse_task(r.get("task_id"), r["sample_id"])
        if ag != agent:
            continue
        by_dim.setdefault(dim_of.get(r["sample_id"], "?"), []).append(r)
    groups = [by_dim[x] for x in sorted(by_dim)]
    for lst in groups:
        for r in lst[:per_dim]:
            refs = r.get("evidence_refs", [])
            res["total_refs"] += len(refs)
            for rid in refs:
                res["checked"] += 1
                if rid not in ids:
                    res["broken"].append(f"{r['sample_id']}/{rid} 不在 evidence.jsonl")
                elif raws.get(rid) and not os.path.exists(os.path.join(run_dir, raws[rid])):
                    res["broken"].append(f"{r['sample_id']}/{rid} raw 缺失")
    return res


def pct(x, digits=0):
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def to_md(rep):
    L = [f"# okb 成绩单 · suite {rep['suite'] or '?'} · 契约 {rep['contract_version'] or '?'}",
         f"> tools/report.py v4.1 ｜ §7 口径：可计分=status ok ∧ verdict∈五类 ∧ judge_error 空；"
         f"低置信=可测<{rep['thresholds']['n']} 或覆盖率<{rep['thresholds']['cov']:.0%}（作用域见 §7:236，"
         f"mock 演练阈值仅为演示）；风险指标单列", ""]
    for r in rep["runs"]:
        ov = r["overall"]
        has_j = any("judged" in d for d in r["dims"].values())
        L.append(f"## Run：{r['label']}")
        score = ov["overall_score"]
        note = f"（{ov.get('note')}）" if ov.get("note") else ""
        L.append(f"**总分**：{'—' if score is None else format(score, '.2f')}"
                 f" · 可用维度 {len(ov['usable_dims'])}/6 {note}")
        head = ("| 维度 | 可测样本/总 | 覆盖率 | 正确率(样本均值) | wp率 | wr率 | 低置信 | withhold wp率 |"
                + (" judged |" if has_j else ""))
        sep = "|" + "---|" * (8 + (1 if has_j else 0))
        L += [head, sep]
        for dim in DIMS:
            d = r["dims"][dim]
            wh = d["withhold"]["wp_rate_within_withhold"]
            row = (f"| {dim} | {d['n_scored']}样本·{d['n_rows']}行/{d['n_total']} | "
               f"{pct(d['coverage'])} | {pct(d['acc'])} | "
                   f"{pct(d['wp_rate'])} | {pct(d['wr_rate'])} | {'⚠' if d['low_confidence'] else '—'} | "
                   f"{pct(wh) if d['withhold']['n_scored'] else '—'} |")
            if has_j:
                row += f" {pct(d['judged']['acc'])} |" if "judged" in d else " — |"
            L.append(row)
        ex = sorted({s for d in r["dims"].values() for s in d["excluded"]})
        if ex:
            L.append(f"\n行级剔除（带归因；「保留N轮」=该样本部分轮仍计入分母）：{', '.join(ex)}")
        if "trace" in r:
            t = r["trace"]
            L.append(f"\n**可追溯抽查**：核验引用 {t['checked']} 处（该 agent 抽中行的引用全集；"
                     f"断链 {len(t['broken'])}" + (f"：{t['broken'][:3]}" if t["broken"] else "") + "）")
        L.append("")
    if "sensitivity" in rep:
        L.append("## 敏感性附录（P3-2）")
        L.append("| 口径 | Run | 总分 | 可用维度 |")
        L.append("|---|---|---|---|")
        for label, runs in rep["sensitivity"].items():
            for rr in runs:
                o = rr["overall"]
                L.append(f"| {label} | {rr['run']} | {'—' if o is None else format(o, '.2f')}"
                         f" | {rr['usable_dims']}/6 |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--thr-n", type=int, default=10)
    ap.add_argument("--thr-cov", type=float, default=0.8)
    ap.add_argument("--sensitivity", action="store_true")
    ap.add_argument("--trace", type=int, default=0)
    ap.add_argument("--no-judged", action="store_true", help="不并读 verdicts.judged.jsonl")
    args = ap.parse_args(argv)
    paper = load_jsonl(args.paper)
    rep, problems = build_report(args.runs.split(","), paper, thr_n=args.thr_n,
                                 thr_cov=args.thr_cov, sensitivity=args.sensitivity,
                                 trace=args.trace, judged_view=not args.no_judged)
    if problems:
        print("行级契约/完整性问题（拒绝出报）：", file=sys.stderr)
        for p in problems[:20]:
            print("  ✗", p, file=sys.stderr)
        if len(problems) > 20:
            print(f"  …共 {len(problems)} 条", file=sys.stderr)
        return 1
    md = to_md(rep)
    if args.out:
        with open(args.out + ".md", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(md)
        with open(args.out + ".json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        print(f"✅ 写出 {args.out}.md / {args.out}.json")
    else:
        print(md)
    broken = sum(len(r.get("trace", {}).get("broken", [])) for r in rep["runs"])
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
