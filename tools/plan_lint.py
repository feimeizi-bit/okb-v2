#!/usr/bin/env python3
"""okb · 计划层 requirement lint（判官口径防线）。

四层评分把判官回答 supported=通过 / contradicted=失败，因此每条 semantic
requirement 必须是「可判真假的合规命题」，并显式给出 supported/contradicted
映射；否则判官按字面读会与管线语义相反（2026-10-13 审计：552 任务中 17 个
被反判判死、2 个假通过）。

规则（error）：
- E201 元注释/裸失败类：requirement 含作者速记、裁决注记、lint 代号、裸
  `=omitted|=confused|=wp|=wr|=soft` 等，判官无法二值化。
- E202 极性缺映射：basis 标明派生自 expect=absent，但 requirement 未给出
  supported 与 contradicted 两端映射（正确方向不可自明）。

用法：python tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json [--json]
退出码：0 无 error / 1 有 error / 2 输入不可读。
"""
from __future__ import annotations

import argparse
import json
import re
import sys

META_RE = re.compile(
    r"(降\s*soft|降软|裁决|交判官|不误杀|即判|"
    r"=wp|=wr|=omitted|=confused|=soft|"
    r"D\d+\s*[：:]|D\d+\s*改|D\d+\s*(新增|收窄)|"
    r"E1\d\d|W20\d|防线)"
)
ABSENT_MARK = "expect=absent"


def lint_plan(plan: dict) -> list[dict]:
    problems = []
    for row in plan.get("samples") or []:
        sid = row.get("sample_id")
        for c in row.get("checks") or []:
            if c.get("layer") != "semantic":
                continue
            req = c.get("requirement") or ""
            basis = c.get("basis") or ""
            m = META_RE.search(req)
            if m:
                problems.append({
                    "level": "error", "code": "E201", "sample_id": sid, "check_id": c.get("id"),
                    "message": "requirement 含元注释/裸失败类写法 %r：判官口径必须是合规命题+supported/contradicted 映射" % m.group(0),
                })
            if ABSENT_MARK in basis and not ("supported" in req and "contradicted" in req):
                problems.append({
                    "level": "error", "code": "E202", "sample_id": sid, "check_id": c.get("id"),
                    "message": "absent 派生检查缺 supported/contradicted 显式映射（正确方向不可自明，判官会按字面反判）",
                })
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="计划层 requirement 极性/自足性 lint")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        plan = json.loads(open(args.plan, encoding="utf-8").read())
    except OSError as exc:
        print("输入不可读：%s" % exc, file=sys.stderr)
        return 2
    problems = lint_plan(plan)
    errors = [p for p in problems if p["level"] == "error"]
    if args.json:
        print(json.dumps({"problems": problems, "errors": len(errors)}, ensure_ascii=False, indent=1))
    else:
        for p in problems:
            print("[%s] %s:%s %s" % (p["code"], p["sample_id"], p["check_id"], p["message"]))
        print("plan-lint：%d error / %d warning" % (len(errors), len(problems) - len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
