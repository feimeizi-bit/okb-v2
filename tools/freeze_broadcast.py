#!/usr/bin/env python3
"""生成冻结广播件（F6 第①③④项）：paper / plan / 版本 / 缺口声明，一次出齐。

为什么做成命令：A 要的是"能今晚开跑"的四个值。手抄 sha 已经在我这边错过三次
（0.4659、pilot sha 多一个字符、子命令数 26→27），所以这里**一个 sha 都不手打**，
全部现场读盘；版本名与缺口清单从 `notes/freeze-status-v1.0.json` 取，改卷必改凭据。

产出的东西**不是**"正式卷"：红线未清零前只能定名为开发集版本（见回执 §3），
数据卡里把未达标项原样写出来，评审读到的就是缺口而不是结论。

数据卡里的每一句"未达标/达标"都由 `--status` 那份凭据的数字**现场拼**，不写死文案：
上一版把"memory_recall 只有 1 条混淆判据、similar_discrimination 有 26 条"写进正文，
结果凭据换成三读法之后那句话就成了没人对的旧数（写死的散文一定会过期，数字不会）。
`--paper-yaml/--paper-jsonl/--plan` 可指定，用于 dev.2 这类"卷不变、只换计划"的版本。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 默认值 = dev.1 交付卷三件套；dev.2 用 --plan 换计划（卷逐字节不变，见预注册 §8.4）
PAPER_YAML = "samples/paper-r1.yaml"
PAPER_JSONL = "build/paper-r1.jsonl"
PLAN = "samples/four-layer-r1.json"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rl4_line(st: dict) -> str:
    """红线4 一行话：把"校准了多少、死锚几条、还差什么"全带上，不写成"有凭据/无凭据"。"""
    r = st["red_line_4_anchorcal"]
    if not r.get("credential_for_this_paper"):
        return "**无本卷凭据**（`notes/*anchorcal*.json` 里没有 paper_sha256 对得上这份卷的）"
    return ("凭据 `%s`（真实批 `%s` / run_id `%s`）：可判 **%s/%s** 条锚点、死锚嫌疑 **%s** 条、"
            "不可判 **%s** 条；真实批只覆盖 **%s/%s** 题 ⇒ %s"
            % (r.get("credential"), r.get("run_dir"), r.get("run_id"),
               r.get("decidable_anchors"), r.get("anchors_total"),
               r.get("suspect_dead_predicate"), r.get("undecidable_total"),
               r.get("samples_covered_by_run"), r.get("samples_total"),
               "**达标**" if r.get("done") else
               "**未达标**（不是没做：其余题的锚点量得到谓词可实现性、量不到证据到位性，"
               "要等覆盖全卷的真实批）"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default="v1.0-dev.1")
    ap.add_argument("--date", default="2026-10-08")
    ap.add_argument("--status", default="notes/freeze-status-v1.0.json")
    ap.add_argument("--paper-yaml", dest="paper_yaml", default=PAPER_YAML)
    ap.add_argument("--paper-jsonl", dest="paper_jsonl", default=PAPER_JSONL)
    ap.add_argument("--plan", default=PLAN,
                    help="四层计划；dev.2 = 同一份卷 + samples/four-layer-r1-dev2.json")
    ap.add_argument("--provenance", action="append", default=[],
                    help="可重复：本版本的来历/边界声明，原样进广播件与数据卡（不写死在生成器里）")
    ap.add_argument("--out", default=None, help="只打印不写盘时传 --print")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args(argv)

    for rel in (a.paper_yaml, a.paper_jsonl, a.plan, a.status):
        if not (ROOT / rel).is_file():
            print("缺 %s：不猜指纹（先 build_paper / freeze_status）" % rel, file=sys.stderr)
            return 2
    rows = [json.loads(l) for l in (ROOT / a.paper_jsonl).read_text(encoding="utf-8").splitlines() if l.strip()]
    plan = json.loads((ROOT / a.plan).read_text(encoding="utf-8"))
    st = json.loads((ROOT / a.status).read_text(encoding="utf-8"))
    checks = sum(len(s["checks"]) for s in plan["samples"])
    # plan 与卷同源：这是 F7 的硬条件，绑不上就不该广播
    if plan.get("paper_sha256") != sha(ROOT / a.paper_jsonl):
        print("plan.paper_sha256 与编译卷不一致：卷与计划不同源，拒绝广播", file=sys.stderr)
        return 2
    # 凭据也必须量的是这一对卷/计划：拿 dev.1 的凭据给 dev.2 广播 = 缺口张冠李戴
    if st.get("paper_sha256") != sha(ROOT / a.paper_jsonl) or st.get("plan") != a.plan:
        print("凭据 %s 量的不是这一对卷/计划（凭据 paper=%s plan=%s），拒绝广播"
              % (a.status, str(st.get("paper_sha256"))[:12], st.get("plan")), file=sys.stderr)
        return 2
    dims = collections.Counter(r.get("dimension") for r in rows)
    per = st["red_line_1_confusion"]["per_dimension"]

    doc = {
        "format": "okb-freeze-broadcast-v1",
        "version": a.version, "frozen_date": a.date,
        "status": "development-split（红线未清零，非正式 v1.0；见 docs/B-reply-A-freeze-F1-F7-2026-10-08.md）",
        "status_credential": a.status,
        "paper": {"yaml": a.paper_yaml, "yaml_sha256": sha(ROOT / a.paper_yaml),
                  "jsonl": a.paper_jsonl, "jsonl_sha256": sha(ROOT / a.paper_jsonl),
                  "items": len(rows), "suite_version": (rows[0].get("meta") or {}).get("suite_version")},
        "plan": {"path": a.plan, "sha256": sha(ROOT / a.plan),
                 "bound_paper_sha256": plan.get("paper_sha256"),
                 "mandatory_checks": checks},
        "run_math": {"agents": 2, "repeats": 3, "planned_units": len(rows) * 2 * 3},
        "provenance": list(a.provenance),
        "declared_gaps": {
            "quota_unmet": st["totals"]["quota_unmet"],
            "deep_slots": st["deep_slots"],
            "red_line_1_dims_ok": "%d/%d" % (st["red_line_1_confusion"]["dims_ok"],
                                             st["red_line_1_confusion"]["dims_total"]),
            "red_line_1_readings": {
                k: {"dims_ok": "%d/%d" % (v["dims_ok"], st["red_line_1_confusion"]["dims_total"]),
                    "per_dimension": v["per_dimension"]}
                for k, v in st["red_line_1_confusion"]["readings"].items()},
            "red_line_1_acceptance_reading": st["red_line_1_confusion"]["acceptance_reading"],
            "symmetry": {"gap_literal": st["red_line_2_symmetry"]["gap_literal"],
                         "gap_lenient": st["red_line_2_symmetry"]["gap_lenient"],
                         "allowed": st["red_line_2_symmetry"]["allowed_gap"]},
            "expect_cells_full_suite": st["red_line_3_expect_cells"]["exists"],
            "anchorcal": {"done": bool(st["red_line_4_anchorcal"].get("done")),
                          "decidable_anchors": st["red_line_4_anchorcal"].get("decidable_anchors"),
                          "anchors_total": st["red_line_4_anchorcal"].get("anchors_total"),
                          "undecidable": st["red_line_4_anchorcal"].get("undecidable_total"),
                          "suspect_dead_predicate":
                              st["red_line_4_anchorcal"].get("suspect_dead_predicate"),
                          "samples_covered_by_run":
                              st["red_line_4_anchorcal"].get("samples_covered_by_run"),
                          "samples_total": st["red_line_4_anchorcal"].get("samples_total")},
            "human_review_labels": st["human_review"]["labels_submitted"],
            "items_without_fixture": st["collection_ceiling"]["items_without_fixture"],
            "items_self_declared_unreviewed": st["human_review"]["items_self_declared_not_human_reviewed"]},
        "per_dimension": {d: {"items": dims.get(d, 0),
                              "confusion_judgements": per[d]["confusion_judgements"],
                              "red_line_1_ok": per[d]["red_line_1_ok"],
                              "plan_confused_checks": per[d]["plan_confused_checks"],
                              "red_line_1_ok_B_plan_confused": per[d]["red_line_1_ok_B_plan_confused"],
                              "plan_content_error_checks": per[d]["plan_content_error_checks"],
                              "red_line_1_ok_C_plan_content_error":
                                  per[d]["red_line_1_ok_C_plan_content_error"]} for d in per},
        "reproduce": ["python3 tools/build_paper.py %s -o %s" % (a.paper_yaml, a.paper_jsonl),
                      "sha256sum %s %s %s" % (a.paper_yaml, a.paper_jsonl, a.plan),
                      "python3 tools/freeze_status.py --plan %s --out %s --check"
                      % (a.plan, a.status),
                      "python3 tools/freeze_broadcast.py --version %s --plan %s --status %s "
                      "--print-only" % (a.version, a.plan, a.status)],
    }
    out = a.out or ("notes/freeze-broadcast-%s.json" % a.version)
    if not a.print_only:
        (ROOT / out).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
        card = ROOT / ("docs/data-card-%s.md" % a.version)
        g = doc["declared_gaps"]
        rl1 = st["red_line_1_confusion"]
        readings = rl1["readings"]
        acc = rl1["acceptance_reading"]
        thr = rl1["threshold"]
        s2 = st["red_line_2_symmetry"]
        lines = ["# 数据卡 · %s（由 tools/freeze_broadcast.py 生成，勿手改）" % a.version, "",
                 "> 性质：**开发集卷（development split）**，不是正式 v1.0。未达标项原样列在下面——",
                 "> 评审读到的应当是缺口，不是一个被修饰过的结论。",
                 "> 复算：`%s`" % doc["reproduce"][2],
                 ""]
        if a.provenance:
            lines += ["## 本版本的来历与边界", ""]
            lines += ["- %s" % p for p in a.provenance]
            lines += [""]
        lines += [
                 "| 项 | 值 |", "|---|---|",
                 "| 题数 | %d（suite_version=%s） |" % (doc["paper"]["items"], doc["paper"]["suite_version"]),
                 "| 强制检查 | %d 项 |" % doc["plan"]["mandatory_checks"],
                 "| 卷 sha256 | `%s` |" % doc["paper"]["jsonl_sha256"],
                 "| 卷题卡源 | `%s`（sha256 `%s`） |" % (doc["paper"]["yaml"], doc["paper"]["yaml_sha256"]),
                 "| 计划 | `%s` |" % doc["plan"]["path"],
                 "| 计划 sha256 | `%s`（`paper_sha256` 与卷同源） |" % doc["plan"]["sha256"],
                 "| 就绪度凭据 | `%s` |" % a.status,
                 "| 跑批口径 | %d 题 × 2 Agent × 3 轮 = **%d 任务** |" % (doc["paper"]["items"], doc["run_math"]["planned_units"]),
                 "| 冻结日期 | %s |" % doc["frozen_date"],
                 "",
                 "## 已知缺口（实测，非估计）",
                 "",
                 "- 配额未满足：%s" % ("、".join("`%s` 缺 %d 题" % (d, n)
                                              for d, n in sorted(g["quota_unmet"].items()))
                              or "无"),
                 "- 深化槽：%s" % "、".join("%s %d/%d" % (k, v["measured"], v["required"]) for k, v in g["deep_slots"].items()),
                 "- 红线1（每维混淆判据 ≥%d）：**三读法并报，验收只认 %s**" % (thr, acc),
                 "",
                 "| 维度 | 题数 | A 锚点字面 | B 计划 confused | C 计划内容错（验收） |",
                 "|---|---|---|---|---|"]
        for d in sorted(doc["per_dimension"],
                        key=lambda x: -doc["per_dimension"][x]["plan_content_error_checks"]):
            v = doc["per_dimension"][d]
            mark = lambda ok, n: ("%d ✓" % n) if ok else ("**%d ✗**" % n)   # noqa: E731
            lines.append("| `%s` | %d | %s | %s | %s |" % (
                d, v["items"],
                mark(v["red_line_1_ok"], v["confusion_judgements"]),
                mark(v["red_line_1_ok_B_plan_confused"], v["plan_confused_checks"]),
                mark(v["red_line_1_ok_C_plan_content_error"], v["plan_content_error_checks"])))
        fail_a = sorted(d for d, v in doc["per_dimension"].items() if not v["red_line_1_ok"])
        fail_b = sorted(d for d, v in doc["per_dimension"].items()
                        if not v["red_line_1_ok_B_plan_confused"])
        fail_c = sorted(d for d, v in doc["per_dimension"].items()
                        if not v["red_line_1_ok_C_plan_content_error"])
        lines += ["",
                  "  - 达标维数：A **%s**、B **%s**、C **%s**（共 %d 维）" % (
                      g["red_line_1_readings"]["A_anchors_literal"]["dims_ok"],
                      g["red_line_1_readings"]["B_plan_confused"]["dims_ok"],
                      g["red_line_1_readings"]["C_plan_content_error"]["dims_ok"],
                      rl1["dims_total"]),
                  "  - A 读法不达标维：%s" % (", ".join("`%s`" % d for d in fail_a) or "无"),
                  "  - B 读法不达标维：%s" % (", ".join("`%s`" % d for d in fail_b) or "无"),
                  "  - C 读法（验收）不达标维：%s" % (", ".join("`%s`" % d for d in fail_c) or "无"),
                  "  - 三读法定义与验收依据：`tools/freeze_status.py` 凭据 "
                  "`red_line_1_confusion.readings` / `.acceptance_basis`；"
                  "口径变更登记见 `notes/p5-preregistration-v1.0.md` §8 R-2。",
                  "  - **A 读法未过不因为 C 达标而消失**：它是预注册 §3.1 的字面口径，"
                  "凭据与本卡都照实登出。",
                  "",
                  "- 红线2（正反对称）：正向 %d / 刹车 %d，字面差 **%d**；宽松读法（`boundary_kind=withhold` "
                  "计入刹车）差 **%d**；允许 **%d** → %s。%s" % (
                      s2["by_verdict"]["positive"], s2["by_verdict"]["brake"],
                      s2["gap_literal"], s2["gap_lenient"], s2["allowed_gap"],
                      "两种读法都不达标" if not (s2["ok_literal"] and s2["ok_lenient"]) else "达标",
                      "题级 `expect.verdict` 全为正向，刹车侧只体现在锚点 `on_violation` 上。"
                      if s2["by_verdict"]["positive"] == doc["paper"]["items"] else ""),
                  "- 红线3（每题五档预期格 `%s`）：%s" % (
                      st["red_line_3_expect_cells"]["path"],
                      "**已存在**" if st["red_line_3_expect_cells"]["exists"]
                      else "**未执行**（仅 batchA 5 题有凭据）⇒ 本卷区分度未经写作期预检"),
                  "- 红线4（锚点真实证据形状校准 anchorcal）：%s" % _rl4_line(st),
                  "- 人工审题：**%d 条真人标签**；%d/%d 题的 `meta.limitation` 自述 AI-authored / "
                  "not human-reviewed。" % (g["human_review_labels"], g["items_self_declared_unreviewed"],
                                            doc["paper"]["items"]),
                  "- 采集上限：**%d/%d 题无 fixture** ⇒ objective 层只能断言文件存在（file_exists）。" % (
                      g["items_without_fixture"], doc["paper"]["items"]),
                  "",
                  "## 读数时必须带的解释",
                  ""]
        n = 0
        if fail_c:
            n += 1
            lines.append("%d. 验收读法 C 下仍不达标的维：%s ⇒ 这些维的 `confused` 计数为 0 "
                         "是**判据覆盖面不足，不是智能体没有混淆**；报告与雷达必须标注。" % (
                             n, ", ".join("`%s`" % d for d in fail_c)))
        if fail_a:
            n += 1
            lines.append("%d. 按预注册 §3.1 字面（锚点）口径，%s 的混淆判据仍不足 %d 条。原因不是"
                         "没写判据，而是 %d/%d 题由候选卡编译、其 legacy 锚点只能是**结构锚点**"
                         "（卡片包禁止「字符串出现即判对/判错」），判类实际发生在四层计划"
                         "（`tools/scoring/four_layer.py`：`verdict = failed[0]['on_failure']`）。" % (
                             n, ", ".join("`%s`" % d for d in fail_a), thr,
                             st["provenance"]["items_compiled_from_cards"], st["totals"]["items"]))
        if fail_b:
            n += 1
            lines.append("%d. B 读法（`confused` 单类）下 %s 不达标：该维按 CONTRACT §11 的 "
                         "withhold/release 特殊口径统计，卡片自己声明的内容错类别是 "
                         "`wrongly_persisted` / `wrongly_reused`，把它们改标 `confused` 会污染该维统计。"
                         % (n, ", ".join("`%s`" % d for d in fail_b)))
        if not (s2["ok_literal"] and s2["ok_lenient"]):
            n += 1
            lines.append("%d. 刹车侧题缺失 ⇒ 六维分数当前只支撑「**正向记忆保持度**」，"
                         "不含「该忘的有没有忘」的对称证据；报告标题与雷达轴名不得写成"
                         "「长期记忆能力总分」。" % n)
        if not st["red_line_3_expect_cells"]["exists"]:
            n += 1
            lines.append("%d. 五档区分度未预检 ⇒ 满分题/零分题的「无区分度」风险未被排除（v1.1 待办）。" % n)
        n += 1
        lines.append("%d. 混淆判据的派生台账（逐卡：资格判定、载体、错误对照原文）在覆盖矩阵的 "
                     "`error_control_derivation.per_card`；这些派生是**作者代理判断**、"
                     "未经真人审阅（卡片 `human_truth_label` 全 null），F1 人审可否决任意一条。" % n)
        lines.append("")
        card.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        print("已写 %s" % card.relative_to(ROOT))
    print("=== 冻结广播件（%s / %s）===" % (doc["version"], doc["frozen_date"]))
    print("paper  yaml   %s  %s" % (doc["paper"]["yaml"], doc["paper"]["yaml_sha256"]))
    print("paper  jsonl  %s  %s（%d 题，suite_version=%s）"
          % (doc["paper"]["jsonl"], doc["paper"]["jsonl_sha256"], doc["paper"]["items"],
             doc["paper"]["suite_version"]))
    print("plan           %s  %s（%d 项强制检查，bound_paper_sha256 与卷一致）"
          % (doc["plan"]["path"], doc["plan"]["sha256"], doc["plan"]["mandatory_checks"]))
    print("跑批口径       %d 题 × 2 Agent × 3 轮 = %d 任务" % (doc["paper"]["items"], doc["run_math"]["planned_units"]))
    print("版本性质       %s" % doc["status"])
    if not a.print_only:
        print("已写 %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
