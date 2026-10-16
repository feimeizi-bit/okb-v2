"""WP1: 按 90 核心（六维各15）+ 10 深化 的目标形态，从 WP0 分级结果选举核心/备选池，
并计算新著需求（DU 缺口 + 深化槽缺口）。

输入：00-wp0/wp0-flags.json
输出：wp1-selection.md / wp1-new-needs.md
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
recs = json.loads((BASE / "00-wp0" / "wp0-flags.json").read_text(encoding="utf-8"))

DIMS = ["long_term_retention", "memory_recall", "dynamic_update",
        "similar_discrimination", "boundary_recognition", "task_reuse"]

def rank_key(x):
    p1 = x["flags"].count("P1:")
    notes = x["flags"].count("note:")
    return (0 if x["grade"] == "甲" else 1, -x["plan_content_err"], p1, notes)

lines = ["# WP1 选举与排产（目标：90 核心 = 六维各15 ／ 深化 10）", ""]
new_needs = {"du_core": 10, "deep_time": 2, "deep_strong": 3, "deep_multi": 0}

for d in DIMS:
    sub = [x for x in recs if x["dim"] == d]
    ok = sorted([x for x in sub if x["grade"] in ("甲", "乙")], key=rank_key)
    bad = [x for x in sub if x["grade"] == "丙"]
    if d == "boundary_recognition":
        # 约束：选 15 中 withhold≥10、release≥5
        w = [x for x in ok if x["boundary_kind"] == "withhold"]
        r = [x for x in ok if x["boundary_kind"] == "release"]
        core = w[:10] + r[:5]
        pool = [x for x in ok if x not in core]
    else:
        core = ok[:15]
        pool = ok[15:]
    deficit = max(0, 15 - len(core))
    lines.append(f"## {d}（核心 15；可用 {len(ok)}，丙 {len(bad)}，缺口 {deficit}）")
    lines.append("")
    lines.append("| 入选核心 | 级 | 计划内容错检查 | 旗标 |")
    lines.append("|---|---|---|---|")
    for x in core:
        lines.append(f"| `{x['id']}` | {x['grade']} | {x['plan_content_err']} | {x['flags'][:80]} |")
    if pool:
        lines.append("")
        lines.append("备选池：" + ", ".join(f"`{x['id']}`({x['grade']})" for x in pool))
    if bad:
        lines.append("")
        lines.append("丙级（不入选，重写候选）：" + ", ".join(f"`{x['id']}`" for x in bad))
    lines.append("")

lines.append("## 深化槽审计（目标 时间3 / 强扰动3 / 多步4）")
lines.append("")
deep = [x for x in recs if x["deep_tags"] or x["time_anchor"]]
for x in deep:
    lines.append(f"- `{x['id']}` ({x['dim']}): tags={x['deep_tags'] or '-'} time_anchor={x['time_anchor']}")
lines.append("")
lines.append(f"结论：机器可识别深度标签 {len(deep)} 项（tags/time_anchor）；按冻结基线口径")
lines.append("时间 1/3、强扰动 0/3、多步 8/4 ⇒ 新著需补时间 +2、强扰动 +3（多步已达标，选 4 项标注）。")

(BASE / "01-paper-v2" / "wp1-selection.md").write_text("\n".join(lines), encoding="utf-8")

# 新著需求
need = []
need.append("# WP1 新著需求清单（20 题）")
need.append("")
need.append("> 依据：存量 80 题进核心（含 DU 5 题），DU 缺 10 题；深化 10 = 时间3/强扰动3/多步4")
need.append("> 其中多步 4 可从存量多步题中标注复用，新增 = DU 核心 10 +（时间 2 + 强扰动 3）= 15 题；")
need.append("> 若坚持深化独立成题（不与核心重叠），则新增 20 题。下方按 15 题口径先行。")
need.append("")
need.append("## A. DU 核心补 10 题（dynamic_update）")
need.append("")
scen = [
    "多字段同时更新（地址+电话），旧值不得残留",
    "延迟生效（下月1号起），到期前不得提前用（时间深化+1）",
    "同会话内二次覆盖（先说A后改B），以最后为准",
    "撤回/恢复（说错→纠正→回到原值）",
    "条件性更新（若X则用新值否则用旧值）",
    "单位/格式换算更新（500g→半公斤等），语义等价",
    "版本回退（旧版本重新启用）",
    "跨会话更新（会话1改过一次，会话2再改）",
    "相邻字段不串台（A字段的新值写到B字段=错误）",
    "相对时间更新（下周改到下下周）（时间深化+1）",
]
for i, s in enumerate(scen, 1):
    need.append(f"- N{i:02d} {s}")
need.append("")
need.append("## B. 强扰动深化 3 题（写探之间≥3 个不同话题会话）")
need.append("")
need.append("- D-STR-1 长干扰链：3+ 话题穿插后检索关键记忆（挂 SD 或 LTR）")
need.append("- D-STR-2 噪声冲刷：中间轮含近似词/误导性重复（挂 MR）")
need.append("- D-STR-3 多话题+时间推移复合（挂 DU，可兼时间）")
need.append("")
need.append("## C. 多步复用标注 4 题（从存量多步可达题中选，标注 deep 槽）")
need.append("")
need.append("## D. 出题与验收流水线（每题必过）")
need.append("")
need.append("1. 四类失败形态在计划层全覆盖；2. ≥1 内容谓词判据；3. 路径相对化；")
need.append("4. reply 判据回声自检；5. 时间真实性二选一；6. lint 0 error 且 W206/W207/W208/W212 相关项清零；")
need.append("7. 双读者歧义自查；8. anchorcal 预检（自产证据）通过。")
(BASE / "01-paper-v2" / "wp1-new-needs.md").write_text("\n".join(need), encoding="utf-8")

print("WROTE wp1-selection.md / wp1-new-needs.md")
for d in DIMS:
    sub = [x for x in recs if x["dim"] == d]
    ok = [x for x in sub if x["grade"] in ("甲", "乙")]
    print(f"{d:26s} 可用={len(ok):2d} 核心={min(15,len(ok)):2d} 备选={max(0,len(ok)-15):2d}")
