#!/usr/bin/env python3
"""渲染 docs/paper-r2-cards.md：r2 基线族一页纸，全部字段取自产物，不手抄。

为什么它必须是仓内脚本而不是一次性命令：这页纸写的是题卡字段与读数，一旦不可重生成，
就会变成"文档自己说话"的漂移源（本轮真实发生过一次：页面由临时 heredoc 生成，脚本没留）。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import yaml  # noqa: E402

OUT = ROOT / "docs/paper-r2-cards.md"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = pathlib.Path(argv[argv.index('--out') + 1]) if '--out' in argv else OUT
    extra = yaml.safe_load((ROOT / "samples/paper-r2-extra.yaml").read_text(encoding="utf-8"))
    plan = json.loads((ROOT / "samples/four-layer-r2.json").read_text(encoding="utf-8"))
    cred = json.loads((ROOT / "notes/paper-r2-candidate.json").read_text(encoding="utf-8"))
    att = json.loads((ROOT / "notes/baseline-attribution-mock-r2.json").read_text(encoding="utf-8"))
    mb = {s["sample_id"]: [c for c in s["checks"] if c.get("scope") == "memory_before"]
          for s in plan["samples"]}

    L = ["# r2 基线/准入题族 —— 一页纸（由 `tools/make_r2_cards_doc.py` 从产物生成，不要手改）",
         "",
         "来源：`samples/paper-r2-extra.yaml` + `samples/four-layer-r2.json` + "
         "`notes/paper-r2-candidate.json` + `notes/baseline-attribution-mock-r2.json`；"
         "重新生成：`python3 tools/extend_paper_r2.py && python3 tools/make_r2_cards_doc.py`。",
         "",
         "| 指标 | 实测 |",
         "|---|---|",
         "| r2 题数 / 计划行 / 检查项 | %d / %d / %d |" % (
             cred["samples"], cred["plan_rows"], sum(len(s["checks"]) for s in plan["samples"])),
         "| `memory_before` 检查项 | %d（r1 为 0）|" % cred["scope_counts"]["memory_before"],
         "| r2 考卷 sha256 | `%s…` |" % cred["paper_sha256"][:12],
         "| r2 计划 sha256 | `%s…` |" % cred["plan_sha256"][:12],
         "| r1 是否被改动 | %s（`%s…` / `%s…` / `%s…` 逐字不变）|" % (
             "否" if cred["r1_untouched"] else "**是（异常）**", cred["r1_paper_sha"][:8],
             cred["r1_jsonl_sha"][:8], cred["r1_plan_sha"][:8]),
         "| 离线 mock 归因读数 | %s |" % json.dumps(att["attribution_counts"], ensure_ascii=False),
         "| 评测环境自旗标 | flag_counts=%s，review_queue=%d 条 |" % (
             json.dumps(att.get("flag_counts", {}), ensure_ascii=False),
             att.get("review_queue_size", 0)),
         "",
         "**状态**：候选卷。r1 仍是交付卷；是否取代由队伍决定（切换会使 A 已签发的 `paper_sha256` "
         "预登记失效）。",
         "",
         "## 每题在测什么（判据只取题卡自述文本）",
         ""]
    for s in extra["samples"]:
        a3 = [a for a in s["anchors"] if a["id"] == "A3"][0]
        rows = mb.get(s["id"], [])
        L.append("### `%s` — %s" % (s["id"], s["title"]))
        L.append("")
        L.append("- 维度 / 难度：**%s** / %s；评分路径：%s" % (
            s["dimension"], s["difficulty"], s["score_policy"]))
        L.append("- 会话脚本：")
        for sess in s["script"]:
            for i, tr in enumerate(sess["turns"], 1):
                L.append("  - `%s.t%d` [%s] %s" % (sess["session"], i, sess["phase"], tr["text"]))
        L.append("- 基线锚点 A3：`type=%s expect=%s evaluate_at=%s on_violation=%s`，"
                 "匹配范围 `key=%s, value_regex=%s`" % (
                     a3["type"], a3["expect"], a3["evaluate_at"], a3["on_violation"],
                     a3.get("key"), a3.get("value_regex")))
        L.append("- 派生的四层检查项：%s" % (", ".join(
            "`%s` scope=%s on_failure=%s" % (c["id"], c["scope"], c["on_failure"]) for c in rows) or "—"))
        L.append("- 要求原文（判官看到的就是这段）：%s" % (rows[0]["requirement"] if rows else a3["desc"]))
        L.append("- 正向锚点保留：%s" % ", ".join(
            "`%s`(%s/%s→%s)" % (a["id"], a["type"], a["expect"], a["on_violation"])
            for a in s["anchors"] if a["id"] != "A3"))
        L.append("- 已知限制：%s" % s["meta"]["limitation"])
        L.append("")

    L += ["## 五个状态怎么读（`okb baseline-attribution`）",
          "",
          "| 基线空 | 任务后记忆面非空 | 状态 | 含义 |",
          "|---|---|---|---|",
          "| 是 | 是 | `write_attributed` | 本次召回可归因于本次写入 |",
          "| 是 | 否 | `never_written` | 记忆面从来没有过 → **写入/准入**失效 |",
          "| 否 | 是 | `residue_plus_write` | 基线有残留 → 读数不可归因（污染）|",
          "| 否 | 否 | `residue_cleared` | 任务前有、任务后无 → 异常，需人查 |",
          "| — | — | `capture_gap` | 有写入事件但任务后读到 0 条目 → 仪器自相矛盾，无法归因 |",
          "",
          "判定只看记忆面终态观测，写入事件只用于发现仪器矛盾；**不做正文或关键词匹配**；"
          "计划里没有 `memory_before` 检查项时直接拒绝运行。旗标只进 `review_queue`"
          "（带 `suggested_check`，`counts_toward_score=false`）：它是**对我们自己评测环境的反身检测**，"
          "不指认责任方，也不产生分数。",
          ""]
    out.write_text("\n".join(L), encoding="utf-8", newline="\n")
    print("已生成", out, "行数", len(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
