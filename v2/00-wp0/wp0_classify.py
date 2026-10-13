"""WP0: 旧卷 100 题逐题质检分级（甲/乙/丙）+ 排产统计。

数据源：
- samples/paper-r1.yaml（卷）
- samples/four-layer-r1-dev2.json（计划，339 checks）
- notes/anchorcal-paper-r1.json（锚点校准，257 anchors）
- 00-wp0/lint-local.txt（validate.py 输出，68 warnings）

输出：wp0-table.csv / wp0-flags.json / wp0-summary.md
"""
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(r"D:\project\okb-int")
OUT = Path(r"D:\project\okb-holdings\paper-v2\00-wp0")
sys.path[:0] = [str(REPO / "tools"), str(REPO)]
from loader import load_any  # noqa: E402

rows = load_any(str(REPO / "samples" / "paper-r1.yaml"))[1]
plan = json.loads((REPO / "samples" / "four-layer-r1-dev2.json").read_text(encoding="utf-8"))
ac = json.loads((REPO / "notes" / "anchorcal-paper-r1.json").read_text(encoding="utf-8"))
lint_text = (OUT / "lint-local.txt").read_text(encoding="utf-8", errors="replace")

plan_by = {s["sample_id"]: s for s in plan["samples"]}

ancal_by = {}
for a in ac["per_anchor"]:
    ancal_by.setdefault(a["sample_id"], []).append(a)
path_pinned = {}
for p in ac["path_never_produced"]:
    path_pinned.setdefault(p["sample_id"], []).append(p["anchor_id"])

lint_by = {}
for m in re.finditer(r"\[W(\d+)\] .*?paper-r1\.jsonl:([a-z0-9_]+):", lint_text):
    code, sid = "W" + m.group(1), m.group(2)
    lint_by.setdefault(sid, {})
    lint_by[sid][code] = lint_by[sid].get(code, 0) + 1
print("DEBUG lint_text_len:", len(lint_text), "| lint samples:", len(lint_by), "|", list(lint_by.items())[:2])

FAKE_RE = re.compile(r"(真实经过|真实至少|真实等待|真实的?等待)")

records = []
for r in rows:
    sid = r["id"]
    meta = r.get("meta") or {}
    expect = r.get("expect") or {}
    script = r.get("script") or []
    texts = [t.get("text", "") for s in script for t in s.get("turns", [])]
    n_turns = len(texts)
    n_sessions = len(script)
    n_session_times = sum(1 for s in script if s.get("time"))
    fixture_n = len(r.get("fixture") or [])
    hard = [a for a in (r.get("anchors") or []) if a.get("severity") == "hard"]
    ancal = ancal_by.get(sid, [])
    n_content_pred = sum(
        1 for a in ancal
        if a.get("severity") == "hard" and (a.get("predicate") or ".*") not in ("", ".*")
    )
    ancal_status = {}
    for a in ancal:
        st = a.get("status") or "?"
        ancal_status[st] = ancal_status.get(st, 0) + 1

    p = plan_by.get(sid) or {}
    checks = p.get("checks") or []
    by_fail = {}
    for c in checks:
        by_fail[c.get("on_failure")] = by_fail.get(c.get("on_failure"), 0) + 1
    content_err = sum(by_fail.get(k, 0) for k in ("confused", "wrongly_persisted", "wrongly_reused"))

    lint = lint_by.get(sid, {})
    fake = [t for t in texts if FAKE_RE.search(t)]
    flags = []
    if fake:
        flags.append("P0:fake_real_wait")
    if not checks:
        flags.append("P0:no_plan_checks")
    if n_content_pred == 0 and content_err == 0:
        flags.append("P0:no_content_predicate_anywhere")
    if sid in path_pinned:
        flags.append("P1:path_pinned(" + ",".join(path_pinned[sid]) + ")")
    for code, label in (("W212", "reply_echo"), ("W206", "trap_uncovered"),
                        ("W207", "weak_anchor"), ("W208", "event_anchor_uncalibratable")):
        if lint.get(code):
            flags.append(f"P1:{label}({code}x{lint[code]})")
    for code in ("W202", "W205"):
        if lint.get(code):
            flags.append(f"note:{code}x{lint[code]}")

    if any(f.startswith("P0:") for f in flags):
        grade = "丙"
    elif any(f.startswith("P1:") for f in flags):
        grade = "乙"
    else:
        grade = "甲"

    records.append(dict(
        id=sid, dim=r.get("dimension"), difficulty=r.get("difficulty"),
        grade=grade, flags=";".join(flags),
        source=meta.get("source", ""), author=meta.get("author", ""),
        fixture_n=fixture_n, boundary_kind=expect.get("boundary_kind"),
        deep_tags=",".join(r.get("tags") or []), time_anchor=bool(r.get("time_anchor")),
        n_sessions=n_sessions, n_turns=n_turns, n_session_times=n_session_times,
        anchors_hard=len(hard), anchors_content_pred=n_content_pred,
        ancal_matches_all=ancal_status.get("matches_everything", 0),
        ancal_paths=len(path_pinned.get(sid, [])),
        plan_total=len(checks), plan_content_err=content_err,
        plan_omitted=by_fail.get("omitted", 0), plan_confused=by_fail.get("confused", 0),
        plan_wp=by_fail.get("wrongly_persisted", 0), plan_wr=by_fail.get("wrongly_reused", 0),
        lint_w212=lint.get("W212", 0), lint_w206=lint.get("W206", 0),
        lint_w207=lint.get("W207", 0), lint_w208=lint.get("W208", 0),
        lint_w202=lint.get("W202", 0), lint_w205=lint.get("W205", 0),
        trap_n=len(expect.get("trap") or []), confusions_n=len(expect.get("confusions") or []),
    ))

csv_path = OUT / "wp0-table.csv"
with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
    w.writeheader()
    w.writerows(records[::-1])

(OUT / "wp0-flags.json").write_text(
    json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")

dims = ["long_term_retention", "memory_recall", "dynamic_update",
        "similar_discrimination", "boundary_recognition", "task_reuse"]
lines = ["# WP0 旧卷质检分级汇总", ""]
lines.append("| 维度 | 甲 | 乙 | 丙 | 合计 | 目标核心15缺口 |")
lines.append("|---|---|---|---|---|---|")
total = {"甲": 0, "乙": 0, "丙": 0}
for d in dims:
    sub = [x for x in records if x["dim"] == d]
    g = {k: sum(1 for x in sub if x["grade"] == k) for k in ("甲", "乙", "丙")}
    for k in g:
        total[k] += g[k]
    ok = g["甲"] + g["乙"]
    lines.append(f"| {d} | {g['甲']} | {g['乙']} | {g['丙']} | {len(sub)} | {'+'+str(15-ok) if ok<15 else '0（富余 '+str(ok-15)+'）'} |")
lines.append(f"| **合计** | {total['甲']} | {total['乙']} | {total['丙']} | 100 | — |")
lines.append("")
lines.append("## 缺陷登记（关键词表）")
lines.append("")
lines.append("### P0（丙级：假真实等待 / 无计划检查 / 全卷无内容判据）")
for x in records:
    if x["grade"] == "丙":
        lines.append(f"- `{x['id']}` ({x['dim']}): {x['flags']}")
lines.append("")
lines.append("### P1 路径钉死（anchorcal path_never_produced）")
for x in records:
    if x["ancal_paths"]:
        lines.append(f"- `{x['id']}`: {x['ancal_paths']} 条 — {x['flags']}")
lines.append("")
wk = {"W206": "trap 未被锚点覆盖", "W207": "锚点无区分力", "W208": "事件锚点不可校准", "W212": "reply 与参考答复共享词元（回声）"}
for code, label in wk.items():
    hits = [x for x in records if x[f"lint_{code.lower()}"]]
    lines.append(f"### {code} {label}（{len(hits)} 题）")
    lines.append("- " + ", ".join(f"`{x['id']}`x{x[f'lint_{code.lower()}']}" for x in hits))
    lines.append("")
bk = {}
for x in records:
    if x["boundary_kind"]:
        bk[x["boundary_kind"]] = bk.get(x["boundary_kind"], 0) + 1
lines.append(f"## 边界口径计数（withhold/release 目标 ≥10/≥5）：{json.dumps(bk, ensure_ascii=False)}")
deep = [x for x in records if x["deep_tags"] or x["time_anchor"]]
lines.append("")
lines.append("## 深化标签现有项（tags/time_anchor）")
for x in deep:
    lines.append(f"- `{x['id']}` ({x['dim']}): tags={x['deep_tags'] or '-'} time_anchor={x['time_anchor']}")
(OUT / "wp0-summary.md").write_text("\n".join(lines), encoding="utf-8")
print("WROTE", csv_path)
print("grades:", total)
print("by dim:", {d: {k: sum(1 for x in records if x['dim']==d and x['grade']==k) for k in ('甲','乙','丙')} for d in dims})
print("boundary:", bk)
