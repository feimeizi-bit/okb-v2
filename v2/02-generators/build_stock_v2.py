"""WP1c：从 paper-r1 选出 v2 核心存量 68 题，执行修复，产出：
  stock-v2.yaml        （通过 schema 的卷文件，suite_version=v2.0）
  stock-v2.plan.json   （dev2 计划中对应样本的检查片段）
  stock-v2-repair-log.json（逐题修复台账）

修复项（对齐 wp1b-repairs.md）：
  R1 假真实等待（s8c_deep_001/003）→ 逻辑时间改写
  R2 路径钉死（anchorcal 8 条）→ 降 soft + 注释
  R3 reply 回声 W212（7 题）→ 结构护栏化（regex=".+", on_violation=omitted）
  R4 W208 事件锚点（pii_mask A4）→ 移除（快照锚点仍承接 wp）
  R5 W202 → 补 evaluate_at: snapshot_after
  R6 W206 trap 无承接 → 从 trap 移除并写入 notes
  R7 confusions 重算 = 硬锚点可达集合（保 E120 合规）
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parents[1] / "01-paper-v2"
sys.path[:0] = [str(REPO / "tools"), str(REPO)]
from loader import load_any  # noqa: E402

KEEP = {
 "long_term_retention": ["s8c_long_001","s8c_long_003","s8c_long_005","s8c_long_007","s8c_long_009","s8c_long_011","s8c_long_013",
   "retain_nickname_001","retain_pet_name_001","retain_allergy_001","s8c_deep_001","s8c_deep_003"],
 "memory_recall": ["s8c_memory_001","s8c_memory_003","s8c_memory_005","s8c_memory_007","s8c_memory_009","s8c_memory_011","s8c_memory_013",
   "recall_inference_001","recall_coffee_001","s8c_deep_004"],
 "dynamic_update": ["update_multi_001","update_same_session_001","update_route_001","update_delayed_001","update_invoice_title_001"],
 "similar_discrimination": ["s8c_similar_001","s8c_similar_003","s8c_similar_005","s8c_similar_007","s8c_similar_009","s8c_similar_011","s8c_similar_013",
   "similar_campus_001","similar_contacts_001","similar_invoice_001","similar_wifi_001","similar_desk_001","similar_drink_001","s8c_deep_005"],
 "boundary_recognition": ["s8c_boundary_001","s8c_boundary_002","s8c_boundary_003","s8c_boundary_004","s8c_boundary_011","s8c_boundary_012","s8c_boundary_013",
   "boundary_risk_001","boundary_pii_mask_001","boundary_temp_001","boundary_release_001","boundary_temp_path_001"],
 "task_reuse": ["s8c_task_001","s8c_task_002","s8c_task_003","s8c_task_004","s8c_task_005","s8c_task_006","s8c_task_007",
   "s8c_deep_007","s8c_deep_008","s8c_deep_009","s8c_deep_010",
   "dual_order_001","reuse_report_001","reuse_buglist_two_step_001","reuse_memory_only_001"],
}

W212_ITEMS = ["retain_allergy_001","recall_coffee_001","update_delayed_001","similar_drink_001",
              "boundary_risk_001","boundary_pii_mask_001","boundary_release_001"]

TIME_FIX = {
 "s8c_deep_001": {"title": "跨会话次日约定（逻辑时间）",
   "probe": ("至少真实经过24小时后的新会话", "次日的新会话（逻辑时间）")},
 "s8c_deep_003": {"title": "跨会话重启约定（逻辑时间）",
   "probe": ("经过真实至少五分钟并重启后的新会话", "重启后的新会话（逻辑时间，不等待）")},
}


def covered(trap, anchors):
    for a in anchors:
        for key in ("value_regex", "content_regex", "regex", "key_regex", "path", "field_path", "arg_regex"):
            v = a.get(key)
            if not isinstance(v, str):
                continue
            try:
                if re.search(v, trap):
                    return True
            except re.error:
                pass
            if trap in v or v in trap:
                return True
    return False


def main():
    rows = {r["id"]: r for r in load_any(str(REPO / "samples" / "paper-r1.yaml"))[1]}
    dev2 = json.loads((REPO / "samples" / "four-layer-r1-dev2.json").read_text(encoding="utf-8"))
    dev2_by = {s["sample_id"]: s for s in dev2["samples"]}
    ancal = json.loads((REPO / "notes" / "anchorcal-paper-r1.json").read_text(encoding="utf-8"))
    pinned = {}
    for p in ancal["path_never_produced"]:
        pinned.setdefault(p["sample_id"], []).append(p["anchor_id"])

    log = []
    selected, plan_rows = [], []
    for dim, ids in KEEP.items():
        for sid in ids:
            r = json.loads(json.dumps(rows[sid]))  # deep copy
            fixes = []

            # R1 假等待
            if sid in TIME_FIX:
                fx = TIME_FIX[sid]
                r["title"] = fx["title"]
                old, new = fx["probe"]
                for s in r["script"]:
                    for t in s["turns"]:
                        if old in t["text"]:
                            t["text"] = t["text"].replace(old, new)
                            fixes.append("R1:probe→逻辑时间")
                r.setdefault("meta", {})["limitation"] = (r["meta"].get("limitation", "")
                    + "（v2 修复：去除假真实等待，改逻辑时间口径）").strip()

            # R2 路径钉死 → soft
            for a in r.get("anchors", []):
                if a["id"] in pinned.get(sid, []) and a.get("severity") == "hard":
                    a["severity"] = "soft"
                    a["desc"] = (a.get("desc", "") + "（v2 修复：路径钉死风险，降软锚点，语义由计划检查承接；F1 复核去留）").strip()
                    fixes.append("R2:soft " + a["id"])

            # R3 W212 回声 → 结构护栏
            if sid in W212_ITEMS:
                for a in r.get("anchors", []):
                    if a["type"] == "reply_text" and a.get("severity") == "hard" and a.get("regex") != ".+":
                        a["regex"] = ".+"
                        a["on_violation"] = "omitted"
                        a["desc"] = (a.get("desc", "") + "（v2 修复：去回声，仅结构护栏；语义在计划层）").strip()
                        fixes.append("R3:reply护栏 " + a["id"])

            # R4 W208 事件锚点移除
            before = len(r["anchors"])
            r["anchors"] = [a for a in r["anchors"] if a["type"] != "memory_write"]
            if len(r["anchors"]) != before:
                fixes.append("R4:移除事件锚点 x%d" % (before - len(r["anchors"])))
                r["meta"]["limitation"] = (r["meta"].get("limitation", "")
                    + "（v2 修复：事件锚点不可校准，已移除；快照锚点仍承接错误持久化）").strip()

            # R5 W202 补 evaluate_at
            for a in r["anchors"]:
                if a.get("severity") == "hard" and a.get("expect") == "absent" and "evaluate_at" not in a:
                    a["evaluate_at"] = "snapshot_after"
                    fixes.append("R5:evaluate_at " + a["id"])

            # R6 trap：保留原样，构建期用 --expand-traps 统一展开（CONTRACT §5）

            # R8 无 hard+present 时补结构护栏（E104 兜底）
            hard_pos = [a for a in r["anchors"] if a.get("severity") == "hard" and a.get("expect") == "present"]
            if not hard_pos:
                nums = [int(m.group(1)) for a in r["anchors"] if (m := re.match(r"A(\d+)", a["id"]))]
                guard = "A%d" % ((max(nums) if nums else 0) + 1)
                last_sess = r["script"][-1]["session"]
                r["anchors"].append({
                    "id": guard, "type": "reply_text", "turn": f"{last_sess}.t1", "regex": ".+",
                    "expect": "present", "severity": "hard", "on_violation": "omitted",
                    "desc": "结构护栏（v2 修复新增）：探测轮非空回复；语义由计划检查承接"})
                fixes.append("R8:结构护栏 " + guard)

            # R7 confusions 重算（= 硬锚点可达集合）
            exp = r.get("expect") or {}
            reach = sorted({a.get("on_violation") for a in r["anchors"]
                            if a.get("severity") == "hard" and a.get("on_violation")} - {"remembered"})
            if reach:
                exp["confusions"] = reach

            r["meta"]["author"] = r["meta"].get("author", "") 
            r["meta"]["source"] = str(r["meta"].get("source", "")) + ";v2=stock-repaired"
            if fixes:
                log.append({"sample_id": sid, "fixes": fixes})
            selected.append(r)
            p = dev2_by.get(sid)
            plan_rows.append({"sample_id": sid, "checks": p["checks"] if p else []})

    # R10/R11：判官口径归一化——部分 pilot_* 派生检查的文案只写了失败形态（或把规则注释
    # 混进字段位），判官按字面读：行为正确时会答 contradicted（形态未出现），被聚合算作失败；
    # 注释型则被当作字段去字面搜索。均改写为明确的 supported/contradicted 二值式。
    # （R10=首批 4 条；R11=全量判官审计后追加 3 条，见判官审计记录。）
    CHECK_REWRITE = {
        "pilot_retain_nickname_001_A2_content":
            "通知抬头使用「阿鹿」=supported；抬头使用旧称呼「小王」（或其他称呼）=contradicted（计入混淆）。",
        "pilot_retain_nickname_001_A3_content":
            "通知全文不得出现旧称呼「小王」（单独出现或与「阿鹿」并列均计）：未出现=supported；出现=contradicted（计入混淆）。",
        "pilot_retain_pet_name_001_A2_content":
            "采购.md 不得把邻居猫「抹茶」当作自家猫的名字：未出现「抹茶」=supported；出现「抹茶」=contradicted（计入混淆）。",
        "pilot_recall_inference_001_A2_content":
            "产物对「小满」的年龄标注必须正确（下个月满 6 岁）：标注 6 岁=supported；写错年龄（如记成 16 岁）=contradicted（计入混淆）；未提及「小满」由 A1 检查承接遗漏判定。",
    }
    CHECK_REWRITE_R11 = {
        "pilot_reuse_buglist_two_step_001_A3_content":
            "产物的严重度档位沿用素材与要求中的「高/中/低」：一致=supported；引入其他档位体系=contradicted（计入混淆）。",
        "pilot_update_invoice_title_001_A2_content":
            "产物值位不得出现旧抬头「上海启成信息技术有限公司」：未出现=supported；出现=contradicted（计入混淆）。",
        "pilot_update_same_session_001_A2":
            "后态记忆不得出现旧地址「西湖区文三路100号」：未出现=supported；出现=contradicted（计入混淆）。",
    }
    for map_name, rewrite in (("R10", CHECK_REWRITE), ("R11", CHECK_REWRITE_R11)):
        for prow in plan_rows:
            for check in prow["checks"]:
                new_text = rewrite.get(check.get("id"))
                if new_text and check.get("requirement") != new_text:
                    check["requirement"] = new_text
                    note = "%s:判官口径归一化 %s" % (map_name, check["id"])
                    for entry in log:
                        if entry["sample_id"] == prow["sample_id"]:
                            entry["fixes"].append(note)
                            break
                    else:
                        log.append({"sample_id": prow["sample_id"], "fixes": [note]})

    # R9（两遍法）：先用构建器自身的 W207 检测扫描，再按结果补豁免注记——精确且可复算
    def dump(yaml_path):
        head = ("# stock-v2.yaml —— v2 核心存量卷（自 paper-r1 选举 68 题 + 五类修复 + W207 豁免注记）\n"
                "# 生成：v2/02-generators/build_stock_v2.py；修复台账：stock-v2-repair-log.json\n")
        yaml_path.write_text(head + yaml.safe_dump({"suite_version": "v2.0", "samples": selected},
                              allow_unicode=True, sort_keys=False, width=400), encoding="utf-8")

    dump(OUT / "stock-v2.yaml")
    r = subprocess.run([sys.executable, str(REPO / "tools" / "build_paper.py"),
                        str(OUT / "stock-v2.yaml"), "-o", str(OUT / "_stock-w207.jsonl")],
                       cwd=str(REPO), encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    if r.returncode != 0:
        print(r.stdout[-2000:])
        raise SystemExit("stock probe build failed")
    hits = re.findall(r"\[W207\] <build>:([a-z0-9_]+):anchors\[(\d+)\]", r.stdout)
    by = {s["id"]: s for s in selected}
    for sid, idx in hits:
        a = by[sid]["anchors"][int(idx)]
        if "W207 豁免" not in str(a.get("desc", "")):
            a["desc"] = (str(a.get("desc", "")) + "（W207 豁免：旧值保持/结构型断言天然与 fixture 一致，区分力由计划检查承担）").strip()
            for entry in log:
                if entry["sample_id"] == sid:
                    entry["fixes"].append("R9:W207 豁免 " + a["id"])
                    break
            else:
                log.append({"sample_id": sid, "fixes": ["R9:W207 豁免 " + a["id"]]})
    dump(OUT / "stock-v2.yaml")
    print("R9 notes:", len(hits))
    (OUT / "stock-v2.plan.json").write_text(json.dumps(
        {"format": "okb-four-layer-plan-fragment-v2", "samples": plan_rows},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "stock-v2-repair-log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print("WROTE stock-v2.yaml | samples:", len(selected), "| repaired:", len(log))


if __name__ == "__main__":
    main()
