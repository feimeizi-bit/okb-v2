#!/usr/bin/env python3
"""候选卡（cards-r3）→ 正式试卷 paper-r1 编译器。

输入两样，都是只读的：
  ① 候选卡包 candidate-cards.json（100 张 AI 起草、未经人工审阅、runnable=false）；
  ② 已实测可执行样本 samples/pilot-v0.2.yaml（20 条，带 fixture 与 file/tool 锚点），
     本工具把它当作**文件/产物证据的载体**原样并入同一张卷子。
输出：samples/paper-r1.yaml（题）→ 交给 tools/build_paper.py 编成
build/paper-r1.jsonl（引擎读的 JSONL）→ 它的 sha256 绑进 samples/four-layer-r1.json
（四层评分计划）→ 实测计数与缺口写进 notes/paper-r1-coverage-matrix.json。

默认选择策略（全部可用参数复算，脚本不读任何人工判断）
  * 逐张独立过闸，闸序固定：time_semantics → 真实等待 → 进程重启 → 人工审阅开关 →
    phase → 会话/轮次元数据 → fixture → difficulty → 可锚定要求 → family → 维度配额 → 总量。
    被拒时**所有命中的原因码都记录**，primary_reason 取第一个。
  * 排序键 =（六维枚举序, 卡片 id 字典序），与"哪款 Agent 得分低"无关，可复算。
  * --count（0=不限）总量上限；--per-dimension（0=不限）单维上限；
    --min-per-dimension（默认 1）只用于判定该维是否达标并记缺口，**不为凑数放宽别的闸**。
  * --max-per-family（默认 1）同 family.id 只留排序最靠前的一张，其余记
    duplicate_family_unresolved（实测 100 张 family.id 互不相同 ⇒ 本闸命中 0）。
  * --max-real-wait-seconds（默认 0）：minimum_real_elapsed_seconds 超上限记 long_real_wait；
    requires_process_restart=true 记 requires_process_restart；
    time_semantics.mode 不在 --allow-time-mode（默认只允许 cross_session_only）记
    time_semantics_unsupported。--include-long-wait / --allow-restart / --allow-time-mode 打开。
  * --structural-anchors（默认开）：卡片要求落在 memory_after / agent_reply 这类**四层合法
    scope**上时，试卷 schema 仍要求一条 legacy 锚点（E104/E116：memory 锚点必须给
    key/key_regex，reply 锚点必须给 turn+regex）。卡片包不提供任何正则，且明令禁止
    "字符串出现即判对"。因此这里只补**结构锚点**：
      memory_after → memory_entry, key_regex=".*", expect=present（记忆面存在非空观测条目）
      agent_reply  → reply_text, turn=<probe 会话末轮>, regex=".+"（探测轮必有非空回复）
    它们只断言「有没有观测到记忆面 / 有没有回复」这一结构事实，**不主张任何关键词**；
    语义裁决仍由该要求自己的四层检查承担（requirement 取卡片原文、on_failure 取卡片
    自带的 proposed_failure）。锚点 desc 里就地写明是结构锚点，矩阵单列计数。
    --no-structural-anchors 复算「只有带产物路径的卡能进卷」的旧口径。
  * --extra-suite（可重复）：再原样并入第二份已实测题源（batchA 5 条，带 fixture 与人工锚点），
    同样走「锚点文本级原样搬运 + 由锚点派生四层 plan 行」。
  * --error-control-checks（默认关，v1.0-dev.2 出货口径）：为每张**合格**卡在四层计划里派生
      1 条 on_failure=confused 的语义检查。材料只用卡片自带的两样，一个新关键词都不造：
      authored_error_control.example（出题人登记的错误对照）与 allowed_equivalents（原样嵌进
      判据文本）。资格三条全是机械判定（见 freeze_gates.ERROR_CONTROL_RULES）：有错误对照 /
      该卡没有自己声明内容错类别 / 不是边界识别卡。载体优先级固定：探测轮回复 > 产物 > 记忆面；
      插入在载体检查**之前**（CONTRACT §5 多违规取第一条，更具体的形态放前面）。
      **试卷 yaml/jsonl 逐字节不变**（expect.confusions 由锚点派生，改它会撞 E120）⇒
      dev.2 = dev.1 的卷 + 新计划，A 已采证据与已锁 paper_sha256 继续有效。
      派生台账逐卡进矩阵 error_control_derivation.per_card，含"未经真人审阅"的自述。
  * --require-human-review（默认关）：打开后 review.* 仍有 null（现值全 null）即整卡拒绝，
    原因码 human_review_pending。默认关是本卷口径（开发轮 + 逐条就地披露），
    不是"已审阅"的声明。

card → paper/plan 的字段映射（逐条给判定依据）
  dimension / title   → 原样（六维枚举一致；title 实测 5..12 字符，schema 上限 80）。
  script[]            → 数组顺序 = 会话顺序，session 标签一律重编为 s1..sN。依据：
      tools/validate.py E102 要求会话编号连续，而 83 张卡写成 (s1,s2,s4) 跳号；
      重编是纯机械改名，映射表落进矩阵 compiled_cards[].session_label_map，
      plan 的 basis 同时保留卡片侧与试卷侧两套指针 ⇒ 不丢信息、不造轮次。
  script[].turns[]    → 只取 text。speaker 必须是 user（schema：只能发用户消息），
      id 必须等于 <session>.t<序号>（指针映射的前提）；否则整卡拒绝
      turn_metadata_unmapped / turn_id_inconsistent。
  script[].phase      → 卡片存在 phase="update"（17 张），试卷枚举只有 write/disturb/probe；
      改判会篡改脚本 ⇒ 整卡拒绝 phase_unsupported，不做 update→write 的静默合并。
  script[].time       → 卡片会话级 time 实测全为 null；非 null 时原样带上。
  initial_state.fixture_files → 100 张全空 ⇒ 试卷样本不带 fixture；
      非空 ⇒ 拒绝 fixture_files_unsupported（不搬运卡片里形状未知的文件正文，
      "为过 validate 而编造 fixture 内容"是禁止项）。
  requirements[].scope：
      memory_after / memory_before → 是 four_layer.SCOPES 的合法语义 scope，但试卷 schema 的
          memory_* 锚点必须给 key/key_regex，且 key="*" 还须配 value_regex（E116）。
          卡片包不提供任何正则，且 allowed_equivalents 明确"不使用禁词或字符串出现就判错"
          ⇒ **不得编造正则** ⇒ 该条要求记入 requirement_gaps，原因码 no_anchor_carrier。
      reply_final → agent_reply 的 (session, turn) 由该卡 script 中 **phase=probe 的最后一轮**
          推出（实测 100 张都是"1 个 probe 会话 / 1 轮"，故恒为 probe 末轮，不凭空造轮次）；
          同样因 reply_text 锚点必须给 regex 而记 no_anchor_carrier，推出的 (session, turn)
          仍写进缺口记录以便复核。
      reply_at_<s>_t<n> → 解析出显式 (session, turn)，回查该卡 script 是否存在该轮
          （10 张 boundary 卡的 s2 是 write 会话 ⇒ paper_anchor_turn_allowed=false，
          试卷 E107 只允许锚点引用 probe 会话），一并记录。
      artifact_set / persistent_changes_and_after → **只有该条给出可定位文件路径时才编译**：
          每个路径产出 ①试卷 file_content+path 锚点（expect=present、severity=hard、
          on_violation=该条 proposed_failure、desc=要求原文；file_content 只强制 path，
          不需要正则 ⇒ 无编造）②plan objective file_exists ③plan semantic file_final。
          路径为空 ⇒ no_artifact_path（该卡若无别的可锚定要求即整卡不可编译，E104）；
          路径不合法 ⇒ artifact_path_unsafe。objective 只用 file_exists：
          four_layer.objective 里 file_unchanged 需要初始工件、json_field_equals 需要 .json
          工件正文，而候选卡 0 fixture ⇒ 原因码 no_fixture。
  requirements[].requirement      → 原样进 plan.requirement（不改写、不翻译）。
  requirements[].proposed_failure → 进 on_failure（objective 与 semantic 同值）。
  requirements[].basis（列表）    → plan 的 basis 是字符串 ⇒ 可追溯拼接
      "s1.t1; s4.t1 (paper: s1.t1; s3.t1)"：卡片侧原值一个不丢，试卷侧指针显式给出。
  objective_compile_plan.file_existence → 实测与逐条 paths 完全一致（19 张），本工具
      **以 requirements[].paths 为准**编译，并核对二者一致（差异记入矩阵，不取并集）。
  source.task_and_fact_source_rounds → expect.gold_evidence（同过会话重编映射；
      E113 要求指针可解析）。
  boundary → dimension=boundary_recognition 时进 expect.boundary_kind（E112），缺失即拒绝
      boundary_kind_missing。
  difficulty → 试卷必填但卡片无此字段：只按结构派生"跨会话或有 disturb ⇒ L3"；
      单会话无干扰无法在 L1/L2 间区分 ⇒ 拒绝 difficulty_undecidable，不猜。
  expect.verdict → 卡片未声明人工正解：填 "remembered"，其定义是"没有触发任何已声明失败"
      的机械口径；卡片侧 human_truth_label=null 这一事实写进 meta.limitation 与矩阵，
      不伪装成人工标注。
  pilot-v0.2 的 20 条样本 → **文本级原样并入**：取 samples: 之后的全部字节（含小节注释与
      文件尾遗留说明）直接拼在编译样本之后，不重排字段、不重新生成；矩阵记录被并入样本 id
      清单与逐字节一致结果。注意 build_paper 会把文件级 suite_version 盖进每条样本的
      meta.suite_version（既有工具的派生行为），所以 YAML 逐字节一致而 JSONL 的 meta 不同。
  samples/four-layer-pilot.v1.json 的 2 行人工 disposition → 原样复制进新计划。其余 18 条
      pilot 样本没有现成的四层 plan 行，但它们的 **锚点本身就是人工写好的断言**（desc /
      expect / severity / on_violation / path / turn 齐全，实测 83 个锚点 desc 全非空）⇒
      本工具做**格式派生**（--derive-pilot-rows 默认开，可用 --no-derive-pilot-rows 复算
      「只有 2 行」的旧口径）：requirement 取 anchor.desc **原文**、on_failure 取
      anchor.on_violation **原值**、scope/path/session/turn 由锚点字段机械映射，
      一个 on_failure 都不新造。派生不出的锚点（无 on_violation、或 memory_read/write/
      tool_call 这类事件锚点在 four_layer.SCOPES 里没有对应语义 scope、或 turn="*"）
      一律记入 gaps.pilot_anchor_gaps_by_reason，并作为 role=diagnostic 的 disposition
      保留在计划里（沿用 baseline 计划里 A5 那种 checks=[] 诊断位的既有写法）。
      派生检查项的 basis 里写清来源锚点与其 regex（regex 只作溯源，**不当关键词判据用**，
      卡片侧 allowed_equivalents 明令禁止"字符串出现即判对"）。

退出码：0 = 产物齐且 validate_plan 通过；2 = 输入缺失 / 自检不过（不留半成品）。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

from audit_candidate_quota import DIMENSIONS  # noqa: E402
from build_paper import build  # noqa: E402
from loader import load_any  # noqa: E402
from scoring.four_layer import FAILURES, VERSION, valid_path, validate_plan  # noqa: E402

from freeze_lint import RUBRIC_LINT_RULES, lint_rubrics    # noqa: F401  (题面 lint 已拆出)

from freeze_derive import derive_pilot_checks, pilot_block, dump_samples, HEADER    # noqa: F401  (已拆出，保持旧导入路径可用)

from freeze_gates import Reject, PAPER_PHASES, SESSION_KEYS, RELOCATABLE_SESSION_KEYS, TIME_MODES, SCOPE_MEMORY, SCOPE_ARTIFACT, STRUCTURAL_MEMORY_DESC, STRUCTURAL_REPLY_DESC, REPLY_AT, _sha_text, map_ptr, join_basis, to_session_rows, probe_target, card_time_codes, split_reason, compile_card, select    # noqa: F401  (已拆出，保持旧导入路径可用)
from freeze_gates import (CONTENT_ERROR_CLASSES, ERROR_CONTROL_CARRIER_ORDER,    # noqa: F401
                          ERROR_CONTROL_REQUIREMENT, ERROR_CONTROL_RULES,
                          attach_error_control_check, error_control_eligibility)

MATRIX_FORMAT = "okb-paper-r1-coverage-matrix-v1"
SUITE_VERSION = "v1.0"
EVIDENCE_SOURCES = ["memory_before", "memory_after", "agent_reply", "file_final"]
FAILURE_CODES = ["omitted", "confused", "wrongly_persisted", "wrongly_reused"]
VERDICT_CODES = ["remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"]
# 原因码是本卷唯一的"为什么没进卷子"口径，每条都在模块 docstring 里有判定依据。
REASON_CODES = {
    "time_semantics_unsupported": "time_semantics.mode 不在 --allow-time-mode 白名单内",
    "long_real_wait": "minimum_real_elapsed_seconds 超过 --max-real-wait-seconds",
    "requires_process_restart": "requires_process_restart=true 且未 --allow-restart",
    "phase_unsupported": "script[].phase 不在试卷枚举 write/disturb/probe 内（卡片有 update）",
    "session_metadata_unmapped": "卡片会话上有试卷没有且无法无损搬运的字段",
    "turn_metadata_unmapped": "卡片轮次上有试卷没有的字段，或 speaker 不是 user",
    "turn_id_inconsistent": "卡片轮次 id 与 <session>.t<序号> 不一致，指针映射不可复算",
    "fixture_files_unsupported": "卡片声明了初始文件，本工具不搬运也不编造其正文",
    "no_artifact_path": "没有任何 requirements[].paths 可定位 ⇒ 无 hard+present 锚点（E104）",
    "artifact_path_unsafe": "给出的路径不满足 four_layer.valid_path（试卷 E111 同源约束）",
    "scope_unmapped": "requirements[].scope 不在已知映射表内",
    "no_probe_session": "卡片脚本没有 probe 会话 ⇒ reply_final 的轮次无从推出（不造轮次）",
    "boundary_kind_missing": "boundary_recognition 卡片 boundary 为空（试卷 E112 无法满足）",
    "difficulty_undecidable": "单会话且无 disturb，无法在 L1/L2 之间机械判定",
    "duplicate_family_unresolved": "同 family.id 已被排序更靠前的卡占用",
    "dimension_cap": "该维度已达 --per-dimension 上限",
    "selection_cap": "已达 --count 总量上限",
    "human_review_pending": "--require-human-review 打开且 review.* 仍有 null",
    "no_fixture": "0 初始工件 ⇒ objective 只能 file_exists（file_unchanged/json_field_equals 需可比对工件）",
    "no_anchor_carrier": "要求本身合法，但试卷对应锚点必须给正则/键名，禁止编造 ⇒ 不进强制检查集",
}


def rel(path) -> str:
    """矩阵里一律记仓库相对路径：绝对路径会让同一次编译因调用目录不同而产出不同字节，
    而矩阵本身也必须可复算。"""
    p = pathlib.Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def origin_of(sample_id: str, compiled_ids: set[str]) -> str:
    return "card_compiled" if sample_id in compiled_ids else "pilot_merged"


def build_matrix(args, pkg: dict, cards: list, compiled: list, rejected: list, cap_rejected: list,
                 paper_yaml: pathlib.Path, jsonl: pathlib.Path, plan: dict, pilot_ids: list,
                 merged: list, base_plan: dict) -> dict:
    """矩阵只写实测计数；缺口就是缺口。"""
    by_dim = {d: {"candidate_cards": 0, "compiled_samples": 0, "uncompilable_cards": 0,
                  "cap_rejected_cards": 0, "plan_rows": 0, "checks_objective": 0,
                  "checks_semantic": 0, "artifact_paths": 0, "requirement_gaps": 0,
                  "fixture_files": 0, "paper_samples": 0, "relocated_session_metadata": 0,
                  "plan_rows_from_cards_only": 0} for d in DIMENSIONS}
    for c in cards:
        by_dim[c["dimension"]]["candidate_cards"] += 1
    for r in rejected:
        by_dim[r["dimension"]]["uncompilable_cards"] += 1
    for r in cap_rejected:
        by_dim[r["dimension"]]["cap_rejected_cards"] += 1
    for m in compiled:
        d = by_dim[m["dimension"]]
        d["compiled_samples"] += 1
        d["plan_rows_from_cards_only"] += 1   # plan_rows 统一在下面按实际计划行累加，避免双重计数
        d["artifact_paths"] += len(m["artifact_paths"])
        d["requirement_gaps"] += len(m["requirement_gaps"])
        d["relocated_session_metadata"] = d.get("relocated_session_metadata", 0) + \
            len(m.get("relocated_session_metadata") or [])
        # checks_* 统一由 plan 行累加（见下方 plan["samples"] 循环），此处再累加会双重计数
        d["fixture_files"] += len(m["sample"].get("fixture") or [])

    compiled_ids = {m["card_id"] for m in compiled}
    scopes = {s: {"card_compiled": 0, "pilot_merged": 0, "total": 0} for s in EVIDENCE_SOURCES}
    objective_ops: dict[str, dict[str, int]] = {}
    on_failure = {f: {"card_compiled": 0, "pilot_merged": 0, "total": 0} for f in FAILURE_CODES}
    verdicts = {v: 0 for v in VERDICT_CODES}
    phases: collections.Counter = collections.Counter()
    shapes: collections.Counter = collections.Counter()
    anchor_types: collections.Counter = collections.Counter()

    for row in plan["samples"]:
        origin = origin_of(row["sample_id"], compiled_ids)
        for chk in row["checks"]:
            if chk["layer"] == "objective":
                bucket = objective_ops.setdefault(chk["op"], {"card_compiled": 0,
                                                              "pilot_merged": 0, "total": 0})
            else:
                bucket = scopes[chk["scope"]]
            bucket[origin] += 1
            bucket["total"] += 1
            if chk["on_failure"] in on_failure:
                on_failure[chk["on_failure"]][origin] += 1
                on_failure[chk["on_failure"]]["total"] += 1

    for s in merged:
        verdicts[s["expect"]["verdict"]] += 1
        ph = [x["phase"] for x in s["script"]]
        for p in ph:
            phases[p] += 1
        shapes[origin_of(s["id"], compiled_ids)] += 1
        shapes[("has_disturb" if "disturb" in ph else "no_disturb") +
               ("_cross_session" if len(ph) > 1 else "_single_session")] += 1
        for a in s.get("anchors") or []:
            anchor_types[str(a.get("type"))] += 1

    reasons: collections.Counter = collections.Counter()
    for r in rejected + cap_rejected:
        reasons[r["primary_reason"]] += 1
    gap_req: collections.Counter = collections.Counter()
    for m in compiled:
        for g in m["requirement_gaps"]:
            gap_req[g["reason"]] += 1

    # 六维覆盖必须连 pilot 派生行一起算：by_dim 只数了卡片侧，单看它会让五个维度
    # 显示 0 plan_rows，而实际判据来自并入的 pilot 样本 —— 那是少报，不是缺口。
    dim_of_sample = {s["id"]: s["dimension"] for s in merged}
    for row in plan["samples"]:
        d = by_dim.get(dim_of_sample.get(row["sample_id"], ""), None)
        if d is None:
            continue
        d["paper_samples"] += 1
        d["plan_rows"] += 1
        for chk in row["checks"]:
            if chk["layer"] == "objective":
                d["checks_objective"] += 1
            else:
                d["checks_semantic"] += 1
    for s in merged:
        d = by_dim.get(s["dimension"])
        if d is not None:
            d["paper_samples_all"] = d.get("paper_samples_all", 0) + 1
    below = [d for d in DIMENSIONS
             if by_dim[d]["plan_rows"] < args.min_per_dimension]
    pilot_plan_rows = {r["sample_id"] for r in base_plan["samples"]}
    merged_ids = {s["id"] for s in merged}

    # 混淆判据派生台账（--error-control-checks 关时全为 0，逐条记录仍在，便于复算对照）
    ec_records = [m.get("error_control") for m in compiled]
    ec_records = [r for r in ec_records if r]
    ec_derived = [r for r in ec_records if r.get("derived")]
    ec_by_dim: dict[str, dict[str, int]] = {
        d: {"compiled_cards": 0, "eligible": 0, "derived_checks": 0} for d in DIMENSIONS}
    for m in compiled:
        ec_by_dim[m["dimension"]]["compiled_cards"] += 1
    for r in ec_records:
        if r["eligible"]:
            ec_by_dim[r["dimension"]]["eligible"] += 1
        if r.get("derived"):
            ec_by_dim[r["dimension"]]["derived_checks"] += 1
    ec_skip: collections.Counter = collections.Counter(
        r["skip_reason"] for r in ec_records if not r.get("derived"))
    ec_carrier: collections.Counter = collections.Counter(r["scope"] for r in ec_derived)

    return {
        "format": MATRIX_FORMAT,
        "generated_by": "tools/freeze_candidates.py",
        "inputs": {"cards_file": str(args.cards), "cards_file_is_outside_repo": not pathlib.Path(args.cards).resolve().is_relative_to(ROOT),
                   "cards_sha256": sha256_file(args.cards),
                   "cards_count": len(cards),
                   "cards_package_declared": {"sample_schema_compatible":
                       pkg["sample_schema_compatible"], "runnable": pkg["runnable"],
                       "formal_freeze": pkg["formal_freeze"], "frozen_any": False,
                       "actual_new_human_labels": pkg["actual_new_human_labels"]},
                   "pilot_file": str(args.pilot), "pilot_sha256": sha256_file(args.pilot),
                   "baseline_plan": str(args.baseline_plan),
                   "baseline_plan_sha256": sha256_file(args.baseline_plan),
                   "extra_suite_shifts": getattr(args, "suite_shifts", [])},
        "products": {"paper_yaml": rel(args.out_paper),
                     "paper_yaml_sha256": sha256_file(paper_yaml),
                     "paper_jsonl": rel(args.out_jsonl),
                     "paper_jsonl_sha256": sha256_file(jsonl),
                     "plan": rel(args.out_plan), "plan_format": plan["format"],
                     "plan_paper_sha256": plan["paper_sha256"]},
        "selection_policy": {"count": args.count, "per_dimension": args.per_dimension,
                             "min_per_dimension": args.min_per_dimension,
                             "max_per_family": args.max_per_family,
                             "max_real_wait_seconds": args.max_real_wait_seconds,
                             "include_long_wait": args.include_long_wait,
                             "allow_restart": args.allow_restart,
                             "allow_time_mode": sorted(args.allow_time_mode),
                             "require_human_review": args.require_human_review,
                             "order": "(DIMENSIONS enum order, card id ascending)",
                             "suite_version": args.suite_version},
        "totals": {"candidate_cards": len(cards), "compiled_samples": len(compiled),
                   "uncompilable_cards": len(rejected), "cap_rejected_cards": len(cap_rejected),
                   "paper_samples": len(merged), "plan_rows": len(plan["samples"]),
                   "plan_checks": sum(len(r["checks"]) for r in plan["samples"]),
                   "paper_anchors": sum(len(s.get("anchors") or []) for s in merged),
                   "paper_fixture_files": sum(len(s.get("fixture") or []) for s in merged)},
        "dimensions": by_dim,
        "evidence_sources": {
            "semantic_scopes": scopes, "objective_ops": objective_ops,
            "paper_anchor_types": dict(anchor_types),
            "note": "四类证据来源 = four_layer.SCOPES 的四个语义 scope；objective 检查按 op "
                    "单列（它们按 schema 不允许带 scope 字段）"},
        "stages_and_interference": {
            "paper_session_phases": dict(phases), "sample_shapes": dict(shapes),
            "candidate_cards_with_unsupported_phase": sum(
                1 for r in rejected + cap_rejected if "phase_unsupported" in r["all_reasons"]),
            "card_time_semantics_modes": dict(collections.Counter(
                c["time_semantics"]["mode"] for c in cards))},
        "error_results": {"plan_on_failure_by_check": on_failure,
                          "paper_expect_verdict_by_sample": verdicts,
                          "note": "五类错误结果：on_failure 按检查项计（四层计划的强制检查集），"
                                  "verdict 按样本计（人工标注位，候选卡侧 human_truth_label=null）"},
        "error_control_derivation": {
            "enabled": bool(getattr(args, "error_control_checks", False)),
            "why": "75/100 题的 legacy 锚点只能是结构锚点（卡片包禁止字符串判对/判错），"
                   "按预注册 §3.1 的锚点口径数混淆判据对这些题结构性地数不到"
                   "（memory_recall 全维只有 1 条）；而实际定类发生在四层计划："
                   "tools/scoring/four_layer.py 的 row['verdict'] = failed[0]['on_failure']。"
                   "所以混淆判据补在计划层，材料只用卡片自己写的错误对照与等价口径。",
            "rules": {name: rationale for name, rationale in ERROR_CONTROL_RULES},
            "carrier_precedence": list(ERROR_CONTROL_CARRIER_ORDER),
            "requirement_template": ERROR_CONTROL_REQUIREMENT,
            "content_error_classes": sorted(CONTENT_ERROR_CLASSES),
            "insertion_point": "载体检查之前（CONTRACT §5：多违规取第一条命中，"
                               "作者有责任把更具体的失败形态放前面）",
            "paper_bytes_changed": False,
            "paper_bytes_changed_note": "派生只动四层计划；试卷 yaml/jsonl 与 expect.confusions "
                                        "一个字节都不改（改 expect.confusions 会撞 E120 声明可达性）。"
                                        "因此 dev.2 的「可判失败形态」以计划 on_failure 集合为准，"
                                        "与试卷 expect.confusions 的差异由本台账逐条承载。",
            "derived_total": len(ec_derived),
            "eligible_total": sum(d["eligible"] for d in ec_by_dim.values()),
            "by_dimension": ec_by_dim,
            "carrier_scope_histogram": dict(ec_carrier),
            "skip_reason_histogram": dict(ec_skip),
            "per_card": ec_records,
            "human_review_status": "派生是**作者代理判断**：错误对照由出题卡片登记，"
                                   "但「它属于 confused 而不是别的类」这一步未经真人审阅"
                                   "（卡片 human_truth_label 全 null）；逐条列在 per_card，"
                                   "F1 人审时按这张表复核，可否决任意一条。"},
        "pilot_merge": {"pilot_sample_ids": pilot_ids, "pilot_sample_count": len(pilot_ids),
                        "byte_identical_yaml": bool(args.out_paper.read_text(
                            encoding="utf-8").find(pilot_block(
                                args.pilot.read_text(encoding="utf-8"))) >= 0),
                        "byte_identical_yaml_note": "pilot 块按字节包含于卷尾之前；有 --extra-suite 时"
                                                    "文件最末是最后一个题源的原文块",
                        "method": "paper-r1.yaml 尾部 samples: 之后的块 与 pilot-v0.2.yaml 的"
                                  "同一块逐字节相等（含小节注释与文件尾遗留说明）",
                        "jsonl_meta_note": "build_paper 覆盖 meta.suite_version=" +
                                           str(args.suite_version)},
        "compile_report": {
            "reason_code_definitions": REASON_CODES,
            "primary_reason_histogram": dict(reasons),
            "requirement_gap_reasons": dict(gap_req),
            "uncompilable_cards": rejected,
            "cap_rejected_cards": cap_rejected,
            "compiled_cards": [{"card_id": m["card_id"], "dimension": m["dimension"],
                                "family": m["family"], "artifact_paths": m["artifact_paths"],
                                "session_label_map": m["session_label_map"],
                                "phases": m["phases"],
                                "objective_ops": sorted({c["op"] for c in m["row"]["checks"]
                                                         if c["layer"] == "objective"}),
                                "semantic_scopes": sorted({c["scope"] for c in m["row"]["checks"]
                                                           if c["layer"] == "semantic"}),
                                "file_existence_matches_declared_paths":
                                    m["file_existence_matches_declared_paths"],
                                "requirement_gaps": m["requirement_gaps"]} for m in compiled]},
        "gaps": {
            "candidate_card_fixture_files": {
                "count": sum(len(c["initial_state"]["fixture_files"]) for c in cards),
                "detail": "100 张卡 initial_state.fixture_files 全空 ⇒ 编译样本一条 fixture 都没有"},
            "objective_ops_blocked_by_no_fixture": {
                "count": sum(len(m["artifact_paths"]) for m in compiled), "reason": "no_fixture",
                "detail": "file_unchanged 需要初始工件、json_field_equals 需要 .json 工件正文；"
                          "候选卡两者都没有 ⇒ 编译出的 objective 检查只剩 file_exists"},
            "requirements_without_mandatory_check": {
                "count": sum(len(m["requirement_gaps"]) for m in compiled),
                "reason": "no_anchor_carrier",
                "per_reason": dict(gap_req),
                "detail": "memory/reply 类要求的 legacy 锚点必须带正则或键名，本工具禁止编造 ⇒ "
                          "未进入强制检查集（逐条见 compile_report.compiled_cards）"},
            "dimensions_below_min_per_dimension": {
                "count": len(below), "dimensions": below,
                "detail": "候选卡里可锚定的 19 张全在 task_reuse ⇒ 其余五维本卷只有 pilot 样本"
                          "（判据来自 pilot 自己的人工锚点派生行）"},
            "pilot_samples_without_plan_row": {
                "count": len(merged_ids - pilot_plan_rows),
                "sample_ids": sorted(merged_ids - pilot_plan_rows),
                "detail": "baseline 计划只有 2 行人工写好的 anchor_disposition；本工具不代写语义判断"},
            "plan_rows_vs_paper_samples": {
                "plan_rows": len(plan["samples"]), "paper_samples": len(merged),
                "detail": "score_four_layer 要求 manifest 样本集与 plan selected 集相等 ⇒ 跑批必须 "
                          "--sample-id 指定全部 plan 样本"},
            "human_review": {
                "cards_with_null_review": sum(1 for c in cards
                                              if any(v is None for v in c["review"].values())),
                "runnable_false": sum(1 for c in cards if c["runnable"] is False),
                "frozen_false": sum(1 for c in cards if c["frozen"] is False),
                "split_development": sum(1 for c in cards if c["split"] == "development"),
                "package_actual_new_human_labels": pkg["actual_new_human_labels"],
                "detail": "编译进卷不等于审阅完成；--require-human-review 可把全部候选卡拒掉复算"},
            "family_review": {
                "cards_pending": sum(1 for c in cards
                                     if "review-pending" in str(c["family"]["status"])),
                "duplicate_family_ids": len(cards) - len({c["family"]["id"] for c in cards}),
                "detail": "family.status 全为 author-hypothesis-human-near-duplicate-review-pending；"
                          "同 family 去重闸实测命中 0（100 个 family.id 互不相同）"}},
        "limits": ["本矩阵只统计已落盘产物里的实测计数，不含任何预估",
                   "candidate-cards.json 包级 sample_schema_compatible=false / runnable=false，"
                   "本卷编译的是可机械锚定的子集，其余以原因码留档",
                   "compiled 样本的 legacy 规则路只能判产物是否存在，内容正确性靠四层计划语义检查",
                   "离线 prepare 模式不做语义判定，assessed=0 是预期而不是失败"],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="cards-r3 → paper-r1 编译器（闸序见模块 docstring）")
    ap.add_argument("--cards", type=pathlib.Path,
                    default=pathlib.Path(r"D:/a_shanghai/_review/four-layer/"
                                         "s8-batch18-candidate-design/cards-r3/candidate-cards.json"))
    ap.add_argument("--pilot", type=pathlib.Path, default=ROOT / "samples/pilot-v0.2.yaml")
    ap.add_argument("--baseline-plan", dest="baseline_plan", type=pathlib.Path,
                    default=ROOT / "samples/four-layer-pilot.v1.json")
    ap.add_argument("--out-paper", dest="out_paper", type=pathlib.Path,
                    default=ROOT / "samples/paper-r1.yaml")
    ap.add_argument("--out-plan", dest="out_plan", type=pathlib.Path,
                    default=ROOT / "samples/four-layer-r1.json")
    ap.add_argument("--out-jsonl", dest="out_jsonl", type=pathlib.Path,
                    default=ROOT / "build/paper-r1.jsonl")
    ap.add_argument("--out-matrix", dest="out_matrix", type=pathlib.Path,
                    default=ROOT / "notes/paper-r1-coverage-matrix.json")
    ap.add_argument("--suite-version", dest="suite_version", default=SUITE_VERSION)
    ap.add_argument("--count", type=int, default=0, help="总量上限（0=不限）")
    ap.add_argument("--per-dimension", dest="per_dimension", type=int, default=0,
                    help="单维上限（0=不限）")
    ap.add_argument("--min-per-dimension", dest="min_per_dimension", type=int, default=1,
                    help="只用于判定该维是否达标并记缺口，不为凑数放宽别的闸")
    ap.add_argument("--max-per-family", dest="max_per_family", type=int, default=1)
    ap.add_argument("--max-real-wait-seconds", dest="max_real_wait_seconds", type=int, default=0)
    ap.add_argument("--include-long-wait", dest="include_long_wait", action="store_true")
    ap.add_argument("--allow-restart", dest="allow_restart", action="store_true")
    ap.add_argument("--allow-time-mode", dest="allow_time_mode", action="append",
                    choices=list(TIME_MODES), default=None,
                    help="可重复；默认只允许 cross_session_only")
    ap.add_argument("--require-human-review", dest="require_human_review", action="store_true")
    # 实测结论（不是偷懒）：17 张带 update 的卡里，update 会话排在 disturb 之后
    # （write→disturb→update→probe），映射成 write 会触发 E103「phase 顺序非法」，映射成
    # disturb 又与"写入新值"的语义相反 ⇒ 试卷三相枚举容不下它。默认保持拒收，
    # dynamic_update 维因此只能由 pilot(4)+batchA(1) 承载；这是试卷枚举的限制，记进矩阵。
    ap.add_argument("--map-update-phase", dest="map_update_phase",
                    action=argparse.BooleanOptionalAction, default=False,
                    help="实验：把卡片 phase=update 折进 note 并按 write 承载；"
                         "对 disturb 之后的 update 会撞 E103，默认关")
    ap.add_argument("--structural-anchors", dest="structural_anchors",
                    action=argparse.BooleanOptionalAction, default=True,
                    help="为 memory/回复类要求补结构锚点（默认开）；"
                         "--no-structural-anchors 复算「只有带产物路径的卡能进卷」的旧口径")
    ap.add_argument("--extra-suite", dest="extra_suite", action="append", default=[],
                    help="可重复：把另一份已成题的 suite（如 samples/v1.0-batchA.yaml）"
                         "文本级原样并入卷尾，并为它派生四层 plan 行")
    ap.add_argument("--target-samples", dest="target_samples", type=int, default=0,
                    help="目标题量（>0 时反推卡片配额 = 目标 - 已并入题源数，并校验题数不超）")
    ap.add_argument("--derive-pilot-rows", dest="derive_pilot_rows",
                    action=argparse.BooleanOptionalAction, default=True,
                    help="把 pilot 样本自己的人工锚点派生成四层 plan 行（默认开）；"
                         "--no-derive-pilot-rows 复算「只有 baseline 2 行」的旧口径")
    # 默认关是硬要求：打开后 dev.1 的四份产物就不再逐字节可复现（test_paper_r1_reproducible
    # 钉的是 dev.1 的 sha）。dev.2 用「同一份卷 + 新计划」出货，见模块 docstring 末节。
    ap.add_argument("--error-control-checks", dest="error_control_checks",
                    action=argparse.BooleanOptionalAction, default=False,
                    help="为每张合格候选卡在四层计划里派生 1 条 on_failure=confused 的语义检查，"
                         "材料只取卡片自带的 authored_error_control.example 与 allowed_equivalents"
                         "（不新造关键词）；试卷 yaml/jsonl 字节不变，只有计划变。默认关")
    args = ap.parse_args(argv)
    if args.allow_time_mode is None:
        args.allow_time_mode = ["cross_session_only"]
    return args


def _write_new(path: pathlib.Path, text: str) -> None:
    """新建写入：显式 UTF-8 + LF，跨平台字节确定（与 A 侧 F1 同一口径）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for label, path in (("cards", args.cards), ("pilot", args.pilot), ("baseline plan",
                                                                        args.baseline_plan)):
        if not path.is_file():
            print(f"missing {label} file: {path}", file=sys.stderr)
            return 2
    try:
        pkg = json.loads(args.cards.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"cards file unreadable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    cards = pkg.get("cards")
    if not isinstance(cards, list) or not cards:
        print("cards file has no cards[] list", file=sys.stderr)
        return 2
    for card in cards:
        card["_package_date"] = str(pkg.get("date"))

    # ② 题源装载（先于编译闸：--target-samples 需要知道题源占了几题）
    #    pilot 与 --extra-suite 都是文本级搬运；pilot 样本本就缩进 2 空格可原样缀尾，
    #    0 缩进题源（batchA）整体右移 2 空格才能拼进同一 sequence：位移只改缩进不改字段，
    #    并逐样本 deep-equal 校验，原块/并入块 sha 都记进矩阵（不做"看起来一样"的声明）。
    pilot_text = args.pilot.read_text(encoding="utf-8")
    pilot_tail = pilot_block(pilot_text)
    _, pilot_samples = load_any(str(args.pilot))
    pilot_ids = [s["id"] for s in pilot_samples]
    extra_tail = ""
    extra_samples: list[dict] = []
    suite_shifts: list[dict] = []
    for suite in args.extra_suite:
        spath = pathlib.Path(suite).resolve()
        if not spath.is_file():
            print(f"extra suite not found: {spath}", file=sys.stderr)
            return 2
        raw_block = pilot_block(spath.read_text(encoding="utf-8"))
        _, chunk = load_any(str(spath))
        indented = "".join(("  " + line if line.strip() else line)
                           for line in raw_block.splitlines(keepends=True))
        _, reparsed = load_any(str(spath))
        if reparsed != chunk:
            print("extra suite re-parse differs; refusing to merge", file=sys.stderr)
            return 2
        suite_shifts.append(dict(suite=str(spath), raw_sha256=_sha_text(raw_block),
                                 merged_sha256=_sha_text(indented), indent_shift=2,
                                 samples=len(chunk), field_equal_verified=True))
        extra_tail += indented
        extra_samples.extend(chunk)
    non_card_ids = pilot_ids + [s["id"] for s in extra_samples]
    non_card_samples = list(pilot_samples) + list(extra_samples)
    args.suite_shifts = suite_shifts        # build_matrix 的输入证据（题源缩进位移台账）

    # ① 逐张过闸（编译与配额分离：任何闸不过都不进候选池）
    compiled: list[dict] = []
    rejected: list[dict] = []
    for card in cards:
        try:
            compiled.append(compile_card(card, args))
        except Reject as exc:
            rejected.append(dict(card_id=card["id"], dimension=card["dimension"],
                                 family=card["family"]["id"], title=card.get("title"),
                                 primary_reason=exc.codes[0], all_reasons=list(exc.codes),
                                 requirement_scopes=[r["scope"] for r in card["requirements"]],
                                 artifact_paths=sorted({p for r in card["requirements"]
                                                        for p in (r.get("paths") or [])})))
    if args.target_samples:
        want = args.target_samples - len(non_card_ids)
        if want < 0:
            print(f"target {args.target_samples} 小于已并入题源 {len(non_card_ids)}", file=sys.stderr)
            return 2
        import dataclasses
        args = argparse.Namespace(**{**vars(args), "count": want if not args.count
                                     else min(args.count, want)})
    selected, cap_rejected = select(compiled, args)

    header = HEADER.format(suite=args.suite_version, compiled=len(selected),
                           cards_total=len(cards), rejected=len(rejected) + len(cap_rejected),
                           pilot_count=len(pilot_samples),
                           cards_sha=sha256_file(args.cards), pilot_sha=sha256_file(args.pilot))
    _write_new(args.out_paper,
               header + dump_samples([m["sample"] for m in selected]) + pilot_tail + extra_tail)

    # ③ 编译 + 校验：0 error 才算成卷（warning 允许，但矩阵会记下来）
    if args.out_jsonl.exists():
        args.out_jsonl.unlink()
    rc = build([rel(args.out_paper)], args.out_jsonl, allow_warnings=True)
    if rc != 0:
        print(f"build_paper rejected the compiled paper (rc={rc}); plan/matrix not written",
              file=sys.stderr)
        return 2
    paper = [json.loads(l) for l in
             args.out_jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    paper_sha = sha256_file(args.out_jsonl)

    # ④ 四层计划：编译样本的行 + baseline 里已有的 pilot 行（原样复制，其余 18 条不代写）
    base_plan = json.loads(args.baseline_plan.read_text(encoding="utf-8"))
    rows = [m["row"] for m in selected]
    copied = [r for r in base_plan["samples"] if r["sample_id"] in pilot_ids]
    authored_ids = {r["sample_id"] for r in copied}
    derived_rows: list[dict] = []
    anchor_gaps: list[dict] = []
    unridable_pilot: list[str] = []
    if args.derive_pilot_rows:
        byid = {s["id"]: s for s in non_card_samples}
        for pid in non_card_ids:                   # 顺序 = 题源文件顺序，可复算
            if pid in authored_ids:
                continue
            checks, gaps, disp = derive_pilot_checks(byid[pid])
            anchor_gaps.extend(gaps)
            if not checks:
                unridable_pilot.append(pid)
                continue
            derived_rows.append({"sample_id": pid, "anchor_disposition": disp, "checks": checks})
    plan = dict(format=VERSION, paper_sha256=paper_sha, samples=rows + copied + derived_rows)
    try:
        validate_plan(plan, paper, paper_sha)
    except ValueError as exc:
        print(f"validate_plan rejected the compiled plan: {exc}", file=sys.stderr)
        return 2
    _write_new(args.out_plan, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")

    # ⑤ 覆盖矩阵：只写实测计数
    _, merged = load_any(rel(args.out_paper))
    matrix = build_matrix(args, pkg, cards, selected, rejected, cap_rejected, args.out_paper,
                          args.out_jsonl, plan, non_card_ids, merged, base_plan)
    no_row = [pid for pid in pilot_ids if pid not in authored_ids
              and pid not in {r["sample_id"] for r in derived_rows}]
    matrix["gaps"]["pilot_samples_without_plan_row"] = {
        "count": len(no_row), "sample_ids": no_row,
        "derive_pilot_rows": bool(args.derive_pilot_rows),
        "authored_rows_copied": len(copied),
        "derived_rows": len(derived_rows),
        "derived_checks": sum(len(r["checks"]) for r in derived_rows),
        "detail": ("authored = baseline 里人工写好的 2 行原样复制；derived = 由 pilot 样本自己的"
                   "人工锚点机械派生（requirement/on_failure 全部取锚点原字段）。"
                   "sample_without_derivable_check 里的 pilot 样本一条强制检查都派生不出来，"
                   "进卷但没有评分判据 ⇒ 不能当已评分样本"
                   if args.derive_pilot_rows else
                   "--no-derive-pilot-rows：只有 baseline 2 行有强制检查集，其余 pilot 样本无判据")}
    matrix["gaps"]["pilot_anchor_gaps_by_reason"] = {
        "histogram": dict(collections.Counter(g["reason"] for g in anchor_gaps)),
        "sample_without_derivable_check": unridable_pilot,
        "total_anchors_without_mandatory_check": len(anchor_gaps),
        "detail": "这些锚点是人写好的断言，但四层计划无法表达（事件类无对应语义 scope / "
                  "未声明 on_violation / turn=\"*\" 指不到确切轮次）⇒ 保留为 role=diagnostic，"
                  "不参与强制判定，也不由本工具代做语义裁决"}
    matrix["gaps"]["dimensions_below_min_per_dimension"] = {
        "min_per_dimension": args.min_per_dimension,
        "dimensions_below_by_plan_rows": [d for d in DIMENSIONS
                                         if matrix["dimensions"][d]["plan_rows"]
                                         < args.min_per_dimension],
        "dimensions_below_cards_only": [d for d in DIMENSIONS
                                        if matrix["dimensions"][d]["plan_rows_from_cards_only"]
                                        < args.min_per_dimension],
        "detail": "口径一 plan_rows = 卡片编译行 + pilot 派生行（实际能出判据的维度）；"
                  "口径二 cards_only = 只看候选卡自己能否覆盖该维 ⇒ 五维为 0 是候选卡包的缺口，"
                  "本卷靠并入的 pilot 样本补上判据，不是把缺口藏起来"}
    matrix["gaps"]["paper_phase_enum_limitation"] = {
        "phase_unsupported_cards": sum(1 for r in rejected if "phase_unsupported" in r["all_reasons"]),
        "affected_dimension": "dynamic_update",
        "detail": "试卷 phase 枚举 = write/disturb/probe 且 E103 要求非递减；卡片 update 会话实测"
                  "位于 disturb 之后（write→disturb→update→probe），映射成 write 触发 E103、"
                  "映射成 disturb 与语义相反 ⇒ 整卡拒收，dynamic_update 维判据目前全部来自并入的 "
                  "pilot/batchA 题源。要收这 17 张卡需要 CONTRACT 层扩充 phase 枚举，不是编译器能"
                  "擅自决定的事。",
        "attempt_flag": "--map-update-phase（默认关；开启会撞 E103，保留以便复现该结论）"}
    matrix["gaps"]["rubric_quality"] = lint_rubrics(plan, merged)
    matrix["products"]["plan_copied_pilot_rows"] = len(copied)
    matrix["products"]["plan_rows_from_cards"] = len(rows)
    _write_new(args.out_matrix, json.dumps(matrix, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps({"paper_yaml": rel(args.out_paper), "paper_jsonl": rel(args.out_jsonl),
                      "plan": rel(args.out_plan), "matrix": str(args.out_matrix),
                      "paper_sha256": paper_sha,
                      "cards_total": len(cards), "compiled": len(selected),
                      "rejected": len(rejected), "cap_rejected": len(cap_rejected),
                      "pilot_merged": len(pilot_samples),
                      "plan_rows": len(plan["samples"]), "plan_checks":
                      sum(len(r["checks"]) for r in plan["samples"]),
                      "plan_rows_from_cards": len(rows), "plan_rows_authored_pilot": len(copied),
                      "plan_rows_derived_pilot": len(derived_rows),
                      "pilot_anchors_without_check": len(anchor_gaps),
                      "rubric_flagged_checks": matrix["gaps"]["rubric_quality"]["flagged_checks"],
                      "primary_reason_histogram": matrix["compile_report"]["primary_reason_histogram"],
                      "dimensions": {d: matrix["dimensions"][d]["compiled_samples"]
                                     for d in DIMENSIONS}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
