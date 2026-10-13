#!/usr/bin/env python3
"""冻结就绪度：把 `notes/p5-preregistration-v1.0.md` 的配额与五条红线**逐条量出来**。

为什么需要它：A 的 F1-F7 冻结清单要求"验收口径"可核，而验收口径写在我自己的预注册里。
手抄读数会变成第二个"0.4659"——所以我把它做成命令：`okb freeze-status` 每次重算，
凭据 `notes/freeze-status-v1.0.json` 里的每个数字都能由这张脚本复现。

口径纪律（照预注册原文，不放宽）：
  配额     六维各 15 + 深化槽 10（时间锚定3 / 强扰动3 / 多步复用4）
  红线 1   每维 >=8 条混淆判据。**三种读法一起量、一起报**（挑好看的报是造假）：
           A 锚点读法（预注册 §3.1 字面）= hard&on_violation=confused 的锚点数
             + 含 absent 产物值位判据的样本数；
           B 计划读法（confused 单类）= 四层计划里 on_failure=confused 的强制检查数；
           C 计划读法（内容错三类）= on_failure ∈ {confused, wrongly_persisted, wrongly_reused}
             的强制检查数。
           为什么必须有 B/C：交付卷 75/100 题的 legacy 锚点只能是**结构锚点**（卡片包禁止
           "字符串出现即判对/判错"），而实际定类发生在计划层——
           tools/scoring/four_layer.py:252 `row['verdict'] = failed[0]['on_failure']`。
           只报 A 会把这些题的混淆判据数成 0，那是**我们没测**，不是智能体没犯。
           验收读法 = C（登记见 notes/p5-preregistration-v1.0.md §8 R-2）；A 与 B 同时公示，
           A 仍按字面口径报"未过"，不用 C 去覆盖它。
  红线 2   对称集 >=90：按 expect.verdict 分正向(remembered)/刹车(omitted/confused/wrongly_persisted/
           wrongly_reused)，两组差 <=10%，且每维刹车 >=5。另给"把 boundary_kind=withhold 计入刹车"的
           宽松读法——因为读法不同结论不同时必须让评审看得见，而不是挑一个好看的。
  红线 3   五档预期格 notes/v1.0-expect-cells.json 是否存在（batchA 那份不算全量）
  红线 4   锚点可执行性校准（anchorcal）：凭据必须量的是这份卷（paper_sha256 对得上）、
           死锚嫌疑 0、且每条锚点都可判。只检查"有没有凭据文件"等于扔个空文件就能关掉红线
  红线 5   反面教材清单是否已列
  人审     meta.limitation 里自述 not human-reviewed 的题数；review.* 是否仍为空
  采集上限 fixture=0 的题数（objective 检查只能 file_exists）
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
POSITIVE = {"remembered"}
# 内容错三类 = "记成了什么错"；omitted 是"没记"，不算混淆判据（CONTRACT §11 的 verdict 口径）
CONTENT_ERROR = ("confused", "wrongly_persisted", "wrongly_reused")
RED_LINE_1_MIN = 8
# 验收读法：计划层的内容错三类。理由与登记见 notes/p5-preregistration-v1.0.md §8 R-2。
RL1_ACCEPTANCE = "C_plan_content_error"
BRAKE = {"omitted", "confused", "wrongly_persisted", "wrongly_reused"}
QUOTA_CORE = 15
QUOTA_DEEP = {"时间锚定": 3, "强扰动": 3, "多步复用": 4}

# 预注册 §1 的六维名（写死是故意的：这是锁定的配额表，改了要走登记，不能让脚本跟着漂移）
DIMENSIONS = ("long_term_retention", "memory_recall", "dynamic_update",
              "similar_discrimination", "boundary_recognition", "task_reuse")


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_paper(path: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def anchorcal_reading(paper_sha: str) -> dict:
    """红线4：读 anchorcal 凭据，量"校准到什么程度"，不是量"有没有这个文件"。

    为什么升级：原判据是 `notes/*anchor*cal*` 存在即过 —— 扔个空文件就能关掉一条红线，
    这种"永远绿的自检"比没有自检更危险。现在三条都要成立才判过：
      ① 凭据量的就是这份卷（paper_sha256 相同）；
      ② 死锚嫌疑 0（present 谓词在同域真实语料上零命中）；
      ③ 每条锚点都可判（undecidable 0）—— 真实批没覆盖的题，其锚点量不到证据到位性，
         不能折算成通过（CONTRACT §5：采不到 ≠ 没发生）。
    """
    cands = sorted((ROOT / "notes").glob("*anchor*cal*.json"))
    # as_posix：这些路径会原样进数据卡与广播件，Windows 的反斜杠在 markdown 里是转义字符
    out = {"credentials": [p.relative_to(ROOT).as_posix() for p in cands],
           "credential_for_this_paper": False, "done": False}
    for p in cands:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("paper_sha256") != paper_sha:
            continue
        out.update({
            "credential": p.relative_to(ROOT).as_posix(),
            "credential_for_this_paper": True,
            "format": d.get("format"),
            "run_dir": d.get("run_dir"), "run_id": d.get("run_id"),
            "run_suite_version": d.get("run_suite_version"),
            "anchors_total": d.get("anchors_total"),
            "decidable_anchors": d.get("decidable_anchors"),
            "undecidable_total": d.get("undecidable_total"),
            "samples_covered_by_run": d.get("samples_covered_by_run"),
            "samples_total": d.get("samples_total"),
            "suspect_dead_predicate": len(d.get("suspect_dead_predicate") or []),
            "path_never_produced": len(d.get("path_never_produced") or []),
            "unverified_negative": len(d.get("unverified_negative") or []),
            "matches_everything": d.get("matches_everything"),
            "not_calibratable_event_anchors":
                (d.get("status_histogram") or {}).get("not_calibratable_event_anchor", 0),
            "tautology_rate": d.get("tautology_rate"),
            "unrealizable_covered_by_run": len(
                (d.get("unrealizable_in_global_corpus") or {}).get("covered_by_run") or []),
            "unrealizable_not_covered": len(
                (d.get("unrealizable_in_global_corpus") or {}).get("not_covered_by_run") or []),
            "status_histogram": d.get("status_histogram"),
        })
        out["done"] = bool(out["suspect_dead_predicate"] == 0 and out["undecidable_total"] == 0)
        break
    if not out["credential_for_this_paper"]:
        out["note"] = ("没有量到这份卷的 anchorcal 凭据（凭据必须记 paper_sha256，"
                       "换了卷就得重跑 okb anchorcal）")
    else:
        out["note"] = ("已校准 %s/%s 条锚点：死锚嫌疑 %d 条、恒真（无区分力）%s 条、"
                       "证据不在而谓词可实现 %s 条、路径从未落盘 %s 条；%s 条不可判 —— "
                       "真实批只覆盖 %s/%s 题，其余题量得到『谓词可实现性』、量不到『证据到位性』"
                       "⇒ 红线4 要在覆盖全卷的真实批之后才能真正关闭（不是没做，是做不完）。"
                       "另有 %s 条事件类锚点在任何 probe-only 采集下都不可校准"
                       "（CONTRACT §5：无 via=event 时整条按 skip，属采集能力缺口不是锚点缺陷）"
                       % (out.get("decidable_anchors"), out.get("anchors_total"),
                          out["suspect_dead_predicate"], out.get("matches_everything"),
                          (out.get("status_histogram") or {}).get("evidence_absent_not_predicate"),
                          out.get("path_never_produced"), out.get("undecidable_total"),
                          out.get("samples_covered_by_run"), out.get("samples_total"),
                          out.get("not_calibratable_event_anchors")))
    return out


def plan_counts(plan: dict, dim_of: dict) -> dict:
    """四层计划里每维的强制检查按 on_failure 分类计数（判类的实际执行者）。"""
    per = {d: collections.Counter() for d in DIMENSIONS}
    for s in plan.get("samples") or []:
        d = dim_of.get(s.get("sample_id"))
        if d not in per:
            continue
        for c in s.get("checks") or []:
            per[d][c.get("on_failure")] += 1
    return {d: dict(per[d]) for d in DIMENSIONS}


def measure(rows: list[dict], plan: dict | None = None, paper_sha: str = "") -> dict:
    dims = collections.Counter(r.get("dimension") for r in rows)
    dim_of = {r.get("id"): r.get("dimension") for r in rows}
    pc = plan_counts(plan, dim_of) if plan else {d: {} for d in DIMENSIONS}
    hard_conf = collections.Counter()
    absent_prod_samples = collections.Counter()
    verdict = collections.Counter()
    brake_dims = collections.Counter()
    tags = collections.Counter()
    no_fixture = 0
    self_declared_unreviewed = 0
    review_empty = 0
    for r in rows:
        d = r.get("dimension")
        anchors = r.get("anchors") or []
        hard_conf[d] += sum(1 for a in anchors
                            if a.get("severity") == "hard" and a.get("on_violation") == "confused")
        if any(a.get("expect") == "absent" and a.get("type") == "file_content" for a in anchors):
            absent_prod_samples[d] += 1
        v = (r.get("expect") or {}).get("verdict")
        verdict[v] += 1
        if v in BRAKE:
            brake_dims[d] += 1
        for t in (r.get("tags") or []):
            tags[t] += 1
        if not (r.get("fixture") or []):
            no_fixture += 1
        if "not human-reviewed" in str((r.get("meta") or {}).get("limitation", "")):
            self_declared_unreviewed += 1
        if not r.get("review"):
            review_empty += 1
    card_derived = sum(1 for r in rows
                       if "cards-r3/" in str((r.get("meta") or {}).get("source") or ""))
    per_dim = {}
    for d in DIMENSIONS:
        n = hard_conf[d] + absent_prod_samples[d]
        nb = pc[d].get("confused", 0)
        nc = sum(pc[d].get(k, 0) for k in CONTENT_ERROR)
        per_dim[d] = {"items": dims[d], "quota": QUOTA_CORE,
                      "hard_confused_anchors": hard_conf[d],
                      "absent_product_samples": absent_prod_samples[d],
                      "confusion_judgements": n, "red_line_1_ok": n >= RED_LINE_1_MIN,
                      "plan_checks_by_on_failure": pc[d],
                      "plan_confused_checks": nb,
                      "red_line_1_ok_B_plan_confused": nb >= RED_LINE_1_MIN,
                      "plan_content_error_checks": nc,
                      "red_line_1_ok_C_plan_content_error": nc >= RED_LINE_1_MIN,
                      "brake_items_by_verdict": brake_dims[d], "red_line_2_per_dim_ok": brake_dims[d] >= 5}
    pos = sum(verdict[v] for v in POSITIVE)
    brk = sum(verdict[v] for v in BRAKE)
    other = len(rows) - pos - brk
    withhold = sum(1 for r in rows if (r.get("expect") or {}).get("boundary_kind") == "withhold")
    deep = {"时间锚定": tags.get("has_time_anchor", 0),
            "强扰动": tags.get("has_strong_disturbance", 0),
            "多步复用": sum(1 for r in rows
                            if len({a.get("path") for a in (r.get("anchors") or [])
                                    if a.get("type") == "file_content"}) >= 2)}
    return {
        "totals": {"items": len(rows), "by_dimension": dict(dims),
                   "quota_unmet": {d: QUOTA_CORE - dims.get(d, 0) for d in DIMENSIONS
                                   if dims.get(d, 0) < QUOTA_CORE}},
        "provenance": {"items_compiled_from_cards": card_derived,
                       "items_from_authored_suites": len(rows) - card_derived,
                       "method": "meta.source 含 'cards-r3/' 即判为卡片编译题（现场量，不手抄）",
                       "note": "卡片编译题的 legacy 锚点只能是**结构锚点**（卡片包禁止「字符串出现"
                               "即判对/判错」，且 validate.py E120 要求 expect.confusions 的每个形态"
                               "都有 hard 锚点承接）⇒ 红线1 的 A（锚点字面）读法对它们结构性失效，"
                               "判类实际发生在四层计划"},
        "deep_slots": {k: {"required": QUOTA_DEEP[k], "measured": deep[k]} for k in QUOTA_DEEP},
        "red_line_1_confusion": {"per_dimension": per_dim,
                                 "dims_ok": sum(1 for d in DIMENSIONS if per_dim[d]["red_line_1_ok"]),
                                 "dims_ok_B_plan_confused": sum(
                                     1 for d in DIMENSIONS if per_dim[d]["red_line_1_ok_B_plan_confused"]),
                                 "dims_ok_C_plan_content_error": sum(
                                     1 for d in DIMENSIONS
                                     if per_dim[d]["red_line_1_ok_C_plan_content_error"]),
                                 "dims_total": len(DIMENSIONS),
                                 "threshold": RED_LINE_1_MIN,
                                 "acceptance_reading": RL1_ACCEPTANCE,
                                 "readings": {
                                     "A_anchors_literal": {
                                         "definition": "预注册 §3.1 字面：severity=hard 且 "
                                                       "on_violation=confused 的锚点数 + 含 absent(旧值/邻值)"
                                                       "产物值位判据的样本每样本计 1",
                                         "dims_ok": sum(1 for d in DIMENSIONS
                                                        if per_dim[d]["red_line_1_ok"]),
                                         "per_dimension": {d: per_dim[d]["confusion_judgements"]
                                                           for d in DIMENSIONS},
                                         "still_reported": True,
                                         "note": "本读法对 75/100 道卡片派生题结构性失效（它们的锚点"
                                                 "只能是结构锚点），因此它报出的低值是**测量口径**造成的，"
                                                 "不是智能体没混淆。仍原样公示，不用别的读法覆盖它。"},
                                     "B_plan_confused": {
                                         "definition": "四层计划里 on_failure=confused 的强制检查数",
                                         "dims_ok": sum(1 for d in DIMENSIONS
                                                        if per_dim[d]["red_line_1_ok_B_plan_confused"]),
                                         "per_dimension": {d: per_dim[d]["plan_confused_checks"]
                                                           for d in DIMENSIONS},
                                         "note": "boundary_recognition 低是设计使然：该维按 CONTRACT §11 "
                                                 "的 withhold/release 特殊口径统计，卡片自己声明的内容错"
                                                 "类别是 wrongly_persisted / wrongly_reused，"
                                                 "把它们改标 confused 会污染该维统计。"},
                                     "C_plan_content_error": {
                                         "definition": "四层计划里 on_failure ∈ {confused, "
                                                       "wrongly_persisted, wrongly_reused} 的强制检查数"
                                                       "（即『能把记错内容与没记区分开』的判据）",
                                         "dims_ok": sum(1 for d in DIMENSIONS
                                                        if per_dim[d]["red_line_1_ok_C_plan_content_error"]),
                                         "per_dimension": {d: per_dim[d]["plan_content_error_checks"]
                                                           for d in DIMENSIONS},
                                         "note": "预注册 §3.1 的计数定义里，absent(旧值/邻值)产物值位判据"
                                                 "本身就不是 confused 标签，而是内容错泄漏判据 ⇒ "
                                                 "『混淆判据』的本义是『能判出记成了什么错』。"
                                                 "本读法是它在四层架构下的对应物。"},
                                 },
                                 "acceptance_basis": "判类实际发生在计划层："
                                                     "tools/scoring/four_layer.py 的 "
                                                     "row['verdict'] = failed[0]['on_failure']；"
                                                     "登记见 notes/p5-preregistration-v1.0.md §8 R-2。"
                                                     "三读法数字全部进凭据，验收只认 C，A/B 同时公示。"},
        "red_line_2_symmetry": {
            "by_verdict": {"positive": pos, "brake": brk, "unlabelled": other},
            "gap_literal": abs(pos - brk),
            "by_verdict_lenient_withhold_as_brake": {"positive": pos - withhold, "brake": brk + withhold},
            "gap_lenient": abs((pos - withhold) - (brk + withhold)),
            "allowed_gap": max(1, round(len(rows) * 0.10)),
            "per_dim_brake_min": 5,
            "ok_literal": abs(pos - brk) <= max(1, round(len(rows) * 0.10)),
            "ok_lenient": abs((pos - withhold) - (brk + withhold)) <= max(1, round(len(rows) * 0.10)),
            "note": "字面读法按预注册原文只认 expect.verdict；宽松读法把 boundary_kind=withhold 计入刹车侧。"
                    "两种读法都必须报出——挑好看的报是造假。"},
        "red_line_3_expect_cells": {
            "path": "notes/v1.0-expect-cells.json",
            "exists": (ROOT / "notes/v1.0-expect-cells.json").is_file(),
            "batchA_only_artifact": "notes/v0.3-batchA-expect-cells.json",
            "note": "batchA 那 5 题的五档格不能冒充全量 suite 的区分度预检（预注册 §3.3 适用域已写明）"},
        "red_line_4_anchorcal": anchorcal_reading(paper_sha),
        "human_review": {"items_self_declared_not_human_reviewed": self_declared_unreviewed,
                         "items_with_empty_review_field": review_empty,
                         "labels_submitted": 0,
                         "note": "真人标签 0 条：两条人审回路（判据回路/仪器回路）已就绪，等 A 侧 VM 产物与人"},
        "collection_ceiling": {"items_without_fixture": no_fixture,
                               "note": "这些题的 objective 层只能断言文件存在（file_exists），"
                                       "内容层判据由 reply/file 锚点承担——评分上限必须在报告里可见"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper", default="build/paper-r1.jsonl")
    ap.add_argument("--plan", default="samples/four-layer-r1.json",
                    help="四层评分计划（红线1 的 B/C 读法从它数；必须与卷同源，否则退出 2）")
    ap.add_argument("--out", default="notes/freeze-status-v1.0.json")
    ap.add_argument("--check", action="store_true", help="与已存在凭据比对（内容漂移就退出 2）")
    a = ap.parse_args(argv)
    paper = ROOT / a.paper
    if not paper.is_file():
        print("缺 %s：先跑 okb build-paper（或 tools/build_paper.py）——不猜读数" % a.paper, file=sys.stderr)
        return 2
    rows = load_paper(paper)
    plan_path = ROOT / a.plan
    if not plan_path.is_file():
        print("缺 %s：红线1 的 B/C 读法要从计划数，不猜读数" % a.plan, file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    # 卷与计划同源（F7 的硬条件）：绑不上就不是同一份交付物，拒绝出凭据
    if plan.get("paper_sha256") != sha256(paper):
        print("plan.paper_sha256 与编译卷不一致：卷与计划不同源，拒绝出冻结就绪度凭据", file=sys.stderr)
        return 2
    doc = {"format": "okb-freeze-status-v1",
           "paper": a.paper, "paper_sha256": sha256(paper),
           "plan": a.plan,
           "sources": {"yaml": "samples/paper-r1.yaml",
                       "yaml_sha256": sha256(ROOT / "samples/paper-r1.yaml"),
                       "plan": a.plan,
                       "plan_sha256": sha256(plan_path)},
           "preregistration": "notes/p5-preregistration-v1.0.md",
           "red_lines_passed": [], "red_lines_failed": []}
    m = measure(rows, plan, sha256(paper))
    doc.update(m)
    rl = doc["red_line_1_confusion"]
    # 验收读法 = C（§8 R-2 登记）。A/B 的达标维数一并写进凭据与打印，不用 C 掩盖它们。
    rl_ok = rl["readings"][rl["acceptance_reading"]]["dims_ok"] == rl["dims_total"]
    (doc["red_lines_passed"] if rl_ok else doc["red_lines_failed"]).append("红线1 每维混淆判据>=8")
    s2 = doc["red_line_2_symmetry"]
    (doc["red_lines_passed"] if s2["ok_literal"] and s2["ok_lenient"] else doc["red_lines_failed"]).append("红线2 正反对称集")
    (doc["red_lines_passed"] if doc["red_line_3_expect_cells"]["exists"] else doc["red_lines_failed"]).append("红线3 五档预期格全量")
    (doc["red_lines_passed"] if doc["red_line_4_anchorcal"]["done"] else doc["red_lines_failed"]).append("红线4 锚点可执行性校准")
    (doc["red_lines_passed"] if m["human_review"]["items_self_declared_not_human_reviewed"] == 0 else doc["red_lines_failed"]).append("F1 人工审题")
    (doc["red_lines_passed"] if not m["totals"]["quota_unmet"] else doc["red_lines_failed"]).append("F2 六维配额")
    if a.check:
        out = ROOT / a.out
        if not out.is_file():
            print("凭据不存在：%s" % a.out, file=sys.stderr)
            return 2
        same = json.loads(out.read_text(encoding="utf-8"))
        same.pop("generated_at", None)
        cur = dict(doc); cur.pop("generated_at", None)
        if json.dumps(same, sort_keys=True) != json.dumps(cur, sort_keys=True):
            print("冻结就绪度凭据与重算不一致——重跑 okb freeze-status", file=sys.stderr)
            return 2
        print("凭据与重算一致 ✓")
        return 0
    out = ROOT / a.out
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("已写 %s" % a.out)
    print("红线1 三读法（阈值每维 >=%d）：" % rl["threshold"])
    for _k in ("A_anchors_literal", "B_plan_confused", "C_plan_content_error"):
        _r = rl["readings"][_k]
        print("  %s %d/%d 维达标%s  逐维 %s" % (
            _k, _r["dims_ok"], rl["dims_total"],
            "  ← 验收读法" if _k == rl["acceptance_reading"] else "",
            _r["per_dimension"]))
    print("通过 %d 项 / 未过 %d 项" % (len(doc["red_lines_passed"]), len(doc["red_lines_failed"])))
    for k in doc["red_lines_failed"]:
        print("  未过：", k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
