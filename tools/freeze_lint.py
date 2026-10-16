#!/usr/bin/env python3
"""题面 lint：把"人读起来会怎么误判"变成机器可复算的红旗，不改写任何判据文本。

从 tools/freeze_candidates.py 拆出（该模块承担闸序+派生+搬运+lint 四件事，1189 行）。
拆分的验收标准是**交付卷四份产物逐字节不变**：tools/tests/test_paper_r1_reproducible.py。

注意：lint 只**报告**，绝不改写判据。改写 requirement 会让 request_sha256 变化，
旧的人审答案作废、进行中的测量不可比（措辞问题一律进 rubric-reword 清单）。
"""
from __future__ import annotations

import re
import collections

# 题面质量 lint：判据文本要能被真人和判官**独立读懂**。这些是机器可检的写法问题，
# 只报告、不自动改写（中途改写判据会污染正在进行的测量）。实测数据也显示它们与判官分歧
# 无显著相关（见 _review/four-layer/rubric-quality-vs-agreement-2026-10-04.json），
# 所以定位是"欠技术债"而不是"本次结果的原因"。
RUBRIC_LINT_RULES = (
    ("terse", re.compile(r"^.{0,23}$"), "判据短到不足一句，人和判官都得猜"),
    ("author_shorthand", re.compile(r"[=＝]\s*(wp|wr|om|cf)\b"), "作者速记（=wp/=wr）混进判据"),
    ("internal_reference",
     re.compile(r"裁决第\s*\d+\s*条|\bQ\d+\b|交判官|正则要求|soft\s*$"),
     "内部裁决编号/流程话术出现在判据里"),
)   # 唯一实测多出的会话级字段，无损折进 note


def lint_rubrics(plan: dict, merged: list) -> dict:
    """扫四层计划里的 requirement 文本，输出机器可检的题面质量缺口（不改写任何文本）。"""
    dim_of = {s["id"]: s.get("dimension") for s in merged}
    hits, hist = [], collections.Counter()
    for row in plan["samples"]:
        for chk in row["checks"]:
            text = (chk.get("requirement") or "").strip()
            if chk.get("layer") != "semantic" or not text:
                continue
            codes = [name for name, pat, _ in RUBRIC_LINT_RULES if pat.search(text)]
            if not codes:
                continue
            hist.update(codes)
            hits.append(dict(sample_id=row["sample_id"], check_id=chk["id"],
                             dimension=dim_of.get(row["sample_id"]), flags=codes,
                             on_failure=chk.get("on_failure"), requirement=text[:160]))
    semantic = sum(1 for row in plan["samples"] for c in row["checks"]
                   if c.get("layer") == "semantic" and (c.get("requirement") or "").strip())
    return dict(format="okb-rubric-quality-v1", semantic_checks=semantic,
                flagged_checks=len(hits), flag_histogram=dict(hist),
                flagged_by_dimension=dict(collections.Counter(h["dimension"] or "?" for h in hits)),
                items=hits,
                policy="报告而不改写：判据文本属于题目作者与真人审阅者；本工具只标出机器可检的"
                       "写法问题。中途改判据会让正在进行的测量前后不可比",
                threshold_note=("terse 的 24 字符是**筛选阈值**，不是合格线：中文判据短句也可能"
                                "已经说清，所以这里产出的是待人工过目的清单，不等于这些判据无效。"
                                "实测也未发现写法标记与两个判官分歧显著相关"
                                "（pooled 22/30 vs 8/15，Fisher 双侧 p=0.2004）"),
                reword_target="真人审阅时优先看 internal_reference 与 author_shorthand 两类"
                              "（判据里出现内部编号/速记，外部评审者读不懂），"
                              "terse 类按维度抽样复核即可")


