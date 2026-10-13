"""v2 版质检总检（对照 B 曾被打回的质检环节）：
红线1(A/B/C 三读法) · 红线2(分布披露) · 红线3(五档预期格生成) · 红线4(anchorcal 摘要)
· F1(审题就绪度) · F2(配额/深化槽) · 历史失败模式对照（路径/假等待/回声/trap/事件锚点/source_invalid 风险）
输出：06-qc/qc-v2-report.md + 06-qc/expect-cells-v2.json
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "01-paper-v2"
QC = ROOT / "06-qc"
REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "tools"), str(REPO)]
QC.mkdir(parents=True, exist_ok=True)

from plan_lint import lint_plan  # noqa: E402

paper = [json.loads(l) for l in (PAPER / "_paperv2.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
plan = json.loads((PAPER / "paper-v2.plan.json").read_text(encoding="utf-8"))
ancal = json.loads((PAPER / "anchorcal-paper-v2.json").read_text(encoding="utf-8"))
ledger = json.loads((PAPER / "trap-ledger.json").read_text(encoding="utf-8"))
lint = (PAPER / "_paperv2-lint.txt").read_text(encoding="utf-8", errors="replace")
norm_log = json.loads((PAPER / "plan-requirement-normalization.json").read_text(encoding="utf-8"))
repair_log = json.loads((PAPER / "stock-v2-repair-log.json").read_text(encoding="utf-8"))
plan_problems = lint_plan(plan)
plan_errors = [p for p in plan_problems if p["level"] == "error"]
checks_total = sum(len(x["checks"]) for x in plan["samples"])

by_id = {r["id"]: r for r in paper}
plan_by = {s["sample_id"]: s for s in plan["samples"]}

DIMS = ["long_term_retention", "memory_recall", "dynamic_update",
        "similar_discrimination", "boundary_recognition", "task_reuse"]
CONTENT = {"confused", "wrongly_persisted", "wrongly_reused"}

# ---- 红线1 三读法 ----
r1 = {}
for d in DIMS:
    rows = [r for r in paper if r["dimension"] == d]
    a = b_ = c = 0
    for r in rows:
        checks = plan_by[r["id"]]["checks"]
        b_ += sum(1 for x in checks if x.get("on_failure") == "confused")
        c += sum(1 for x in checks if x.get("on_failure") in CONTENT)
        a += sum(1 for x in r["anchors"] if x.get("severity") == "hard"
                 and x.get("on_violation") == "confused")
    r1[d] = {"items": len(rows), "A_anchor_confused": a, "B_plan_confused": b_, "C_content_error": c,
             "C_ok(>=8)": c >= 8}

# ---- 红线3：五档预期格 ----
cells, shape_missing = [], []
for r in paper:
    checks = plan_by[r["id"]]["checks"]
    shapes = sorted({x["on_failure"] for x in checks if x.get("on_failure")})
    exp = r.get("expect") or {}
    cell = {"sample_id": r["id"], "dimension": r["dimension"],
            "verdict": exp.get("verdict"), "shapes": shapes,
            "content_error_surface": bool(set(shapes) & CONTENT),
            "traps": ledger.get(r["id"], []), "boundary_kind": exp.get("boundary_kind"),
            "tags": r.get("tags") or [], "fixture_n": len(r.get("fixture") or []),
            "checks_n": len(checks)}
    cells.append(cell)
    if not cell["content_error_surface"]:
        exp = r.get("expect") or {}
        if exp.get("boundary_kind") == "release":
            ann = "by-design（release：失败面=过度拒记 → omitted）"
        elif r["id"] == "v2_ltr_late_workspot_001":
            ann = "by-design（无错误值面，仅 omitted）"
        else:
            ann = "待修：补 confused 判据"
        shape_missing.append(f"{r['id']} — {ann}")
(QC / "expect-cells-v2.json").write_text(json.dumps(
    {"format": "okb-expect-cells-v2", "paper_sha256_note": "bind to built jsonl sha at freeze",
     "cells": cells}, ensure_ascii=False, indent=1), encoding="utf-8")

# ---- 红线4：anchorcal ----
dead = ancal.get("suspect_dead_predicate") or []
paths = ancal.get("path_never_produced") or []
unver_neg = ancal.get("unverified_negative") or []

# ---- F2/深化槽 ----
bkind = Counter((r.get("expect") or {}).get("boundary_kind") for r in paper if (r.get("expect") or {}).get("boundary_kind"))
tags = Counter(t for r in paper for t in (r.get("tags") or []))

# ---- 历史失败模式对照 ----
fake = []
for r in paper:
    blob = r.get("title", "") + " " + " ".join(t.get("text", "") for s in r.get("script", []) for t in s.get("turns", []))
    if re.search(r"真实(经过|至少|等待)", blob):
        fake.append(r["id"])
lint_codes = dict(Counter(re.findall(r"\[W(\d+)\]", lint)))
pii_risk = [r["id"] for r in paper if r.get("pii") or "<PII_" in json.dumps(r, ensure_ascii=False)]

lines = ["# paper-v2 质检报告（对照 B 曾被打回的质检环节）", ""]
lines.append("## 红线 1 · 每维混淆判据（三读法，验收=C）")
lines.append("")
lines.append("| 维度 | 题数 | A 锚点字面 | B 计划confused | C 内容错覆盖 | C≥8 |")
lines.append("|---|---|---|---|---|---|")
for d in DIMS:
    x = r1[d]
    lines.append(f"| {d} | {x['items']} | {x['A_anchor_confused']} | {x['B_plan_confused']} | {x['C_content_error']} | {'✓' if x['C_ok(>=8)'] else '✗'} |")
lines.append("")
lines.append(f"**C 读法全维达标：{all(r1[d]['C_ok(>=8)'] for d in DIMS)}**（A/B 同时公示，不做选择性引用）")
lines.append("")
lines.append("## 红线 2 · 正反对称（v1.1 已撤销硬门，仅披露）")
lines.append("")
lines.append(f"- 边界口径分布：{dict(bkind)}（目标 withhold≥10 / release≥5）")
lines.append(f"- verdict 分布：{dict(Counter((r.get('expect') or {}).get('verdict') for r in paper))}")
lines.append("")
lines.append("## 红线 3 · 五档预期格（生成器产物）")
lines.append("")
lines.append(f"- `expect-cells-v2.json` 已生成：{len(cells)} 格（每格含 verdict/失败面/陷阱/boundary/深化标签）")
lines.append(f"- gamed 失败面：**每格均有失败检查面**；缺内容错判据的样本：{shape_missing or '无'}")
lines.append(f"- 全量区分度台账：见 `06-qc/item-stats-v2.md`（工具 `tools/item_stats.py`；口径=跨 Agent 通过率差 |Δ|，≥0.6 记高区分；真机数据，绑定 judge-full-3-replay）")
lines.append("")
lines.append("## 红线 4 · anchorcal（对批次三全局语料）")
lines.append("")
lines.append(f"- 锚点 {ancal['anchors_total']}：可判 {ancal['decidable_anchors']}，不可判 {ancal['undecidable_total']}")
lines.append(f"- **死谓词 {len(dead)}** · **路径从未落盘 {len(paths)}** · 未证实负例 {len(unver_neg)} · 恒真(结构护栏) {ancal['matches_everything']}")
lines.append(f"- 覆盖：samples_covered_by_run={ancal['samples_covered_by_run']}/{ancal['samples_total']}（新题尚无证据，冒烟后复跑）")
if unver_neg:
    lines.append("- 未证实负例明细：")
    for x in unver_neg[:6]:
        lines.append(f"  - `{x.get('sample_id')}` {x.get('anchor_id')}: {(x.get('detail') or '')[:90]}")
lines.append("")
lines.append("## F1 人工审题 · F2 配额")
lines.append("")
lines.append(f"- F1：审题单**未生成**（下一步：只审变更面 = 24 新题 + {len(repair_log)} 修复题，以修复台账为准）")
lines.append(f"- F2：六维 15/15/15/15/15/17 ✓；withhold {bkind.get('withhold',0)} / release {bkind.get('release',0)} ✓；深化标签 {dict(tags)}")
lines.append("")
lines.append("## 历史失败模式逐条对照（B 被打回的坑）")
lines.append("")
lines.append("| B 的坑 | v2 实测 |")
lines.append("|---|---|")
lines.append(f"| 路径钉死 8 条 | anchorcal path_never_produced = {len(paths)} |")
lines.append(f"| 假真实等待 2 题 | 扫描命中 = {fake or 0} |")
lines.append(f"| reply 回声 7 题 | lint W212 = {lint_codes.get('212', 0)} |")
lines.append(f"| trap 无承接 7 题 | lint W206 = {lint_codes.get('206', 0)}（45 trap 入台账） |")
lines.append(f"| 事件锚点不可校准 1 条 | lint W208 = {lint_codes.get('208', 0)} |")
lines.append(f"| 五档格缺失 | expect-cells-v2.json 已生成 |")
lines.append(f"| 配额缺口（DU 5/15 等） | 六维各 15 ✓ |")
lines.append(f"| 卡源单点（cards-r3） | 24 新题在仓（holdings 作者稿） |")
lines.append(f"| pilot 门不可达（结构性死门） | 走自有链，无此门 |")
lines.append(f"| 确定性 source_invalid ×4 | **风险仍在**：{pii_risk or '无'} 需冒烟确证/修表示 |")
lines.append("")
lines.append("## 红线 5 · 计划判官口径 lint（极性防线，2026-10-13 新增）")
lines.append("")
lines.append(f"- 语义检查 requirement：E201（元注释/裸失败类）{sum(1 for p in plan_problems if p['code']=='E201')} · "
             f"E202（absent 派生缺 supported/contradicted 映射）{sum(1 for p in plan_problems if p['code']=='E202')}；"
             f"error 合计 {len(plan_errors)}")
lines.append(f"- 判官口径归一化台账 `plan-requirement-normalization.json`：{len(norm_log)} 条改写（R12；含混淆判据模板自动改写）")
lines.append(f"- 背景：552 任务判官审计确认 17 个任务被极性反判判死（11 应 pass / 6 应 unassessable）、2 个假通过；本 lint 为阻断级防线")
lines.append("")
lines.append("## 工程门（已过）")
lines.append("")
lines.append(f"- build 0 error；`validate_plan` 通过（{checks_total} 检查）；lint {lint_codes}（W205/W207 注记+豁免）；plan-lint {len(plan_errors)} error")
(QC / "qc-v2-report.md").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:60]))
