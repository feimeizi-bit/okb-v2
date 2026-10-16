#!/usr/bin/env python3
"""卡片 → 题卷的闸序与编译（从 tools/freeze_candidates.py 拆出）。

闸序、原因码、会话改名、结构锚点、拒绝条件全部保持逐字不变；验收标准是
tools/tests/test_paper_r1_reproducible.py 要求交付卷四份产物逐字节一致。
为什么留在这里而不是并回主模块：这部分是"数据设计"的判据所在地，改动最频繁、
也最需要被单独测试（tools/tests/test_freeze_candidates.py 直接调 compile_card/select）。
"""
from __future__ import annotations

from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import sys
import yaml  # noqa: E402
from audit_candidate_quota import DIMENSIONS  # noqa: E402
from build_paper import build  # noqa: E402
from loader import load_any  # noqa: E402
from scoring.four_layer import FAILURES, VERSION, valid_path, validate_plan  # noqa: E402
from freeze_lint import RUBRIC_LINT_RULES, lint_rubrics    # noqa: F401  (题面 lint 已拆出)
from freeze_derive import derive_pilot_checks, pilot_block, dump_samples, HEADER    # noqa: F401  (已拆出，保持旧导入路径可用)

class Reject(Exception):
    """一张卡在某个闸上被拒；codes[0] 是主因，其余一并记录。"""

    def __init__(self, *codes: str):
        self.codes = list(codes)
        super().__init__(";".join(codes))


PAPER_PHASES = frozenset({"write", "disturb", "probe"})


SESSION_KEYS = {"session", "phase", "turns", "time", "note"}


RELOCATABLE_SESSION_KEYS = ("purpose",)


TIME_MODES = ("cross_session_only", "actual_elapsed", "logical_calendar_reference")


SCOPE_MEMORY = ("memory_before", "memory_after")


SCOPE_ARTIFACT = ("artifact_set", "persistent_changes_and_after")


STRUCTURAL_MEMORY_DESC = ("structural anchor only: at least one observed persistent-memory "
                          "entry exists after the task; no keyword is asserted (the card package "
                          "forbids 'string appears = correct'), the requirement itself is judged "
                          "by the four-layer semantic check")


STRUCTURAL_REPLY_DESC = ("structural anchor only: the probe turn produced a non-empty reply; "
                         "no keyword is asserted, the requirement itself is judged by the "
                         "four-layer semantic check")


REPLY_AT = re.compile(r"^reply_at_(s[0-9]+)_t([0-9]+)$")


def _sha_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def map_ptr(ptr: str, label_map: dict[str, str]) -> str:
    """s4.t1 + {s4: s3} -> s3.t1；只改会话标签，轮次号一律不动（不造轮次）。"""
    sess, _, turn = str(ptr).partition(".")
    return f"{label_map.get(sess, sess)}.{turn}"


def join_basis(basis: list[str], label_map: dict[str, str]) -> str:
    """plan 的 basis 是字符串：卡片侧原值 + 试卷侧指针，两套都留，便于双向复核。"""
    card_side = "; ".join(basis)
    paper_side = "; ".join(map_ptr(b, label_map) for b in basis)
    return card_side if paper_side == card_side else f"{card_side} (paper: {paper_side})"


def to_session_rows(card: dict, args) -> tuple[list[dict], dict[str, str], list[dict]]:
    """卡片脚本 → 试卷脚本：会话标签连续重编 + 轮次收窄成 text，不确定的字段一律拒卡而不是猜。

    试卷 schema 对 session 与 meta 都是 additionalProperties:false，所以卡片多出的会话级字段
    只有两个去处：折进 session.note 的自由文本（无损、可读、带 [card-meta …] 标记），或者拒卡。
    实测只有 `purpose` 一个键（10 张卡的 temporary_scope_only 边界语义）⇒ 折入 note 并登记台账，
    而不是丢掉这条边界信息；未知键一律拒卡（不猜它该去哪儿）。
    """
    rows: list[dict] = []
    label_map: dict[str, str] = {}
    relocated: list[dict] = []
    for i, sess in enumerate(card["script"], start=1):
        extra = set(sess) - SESSION_KEYS
        if extra - set(RELOCATABLE_SESSION_KEYS):
            raise Reject("session_metadata_unmapped")
        phase = sess["phase"]
        card_phase_tag = None
        if phase not in PAPER_PHASES:
            # 试卷 phase 枚举只有 write/disturb/probe，卡片另有 update（17 张，全在
            # dynamic_update 维）。update 的语义就是"写入新值"，按 write 承载并把它折叠进
            # note 的 [card-meta phase=update] 标记：时序与文本一字不改，原判据词不丢，
            # 且不悄悄把"六维中有一维只能靠 pilot 撑"这件事藏起来。
            if not args.map_update_phase or phase != "update":
                raise Reject("phase_unsupported")
            card_phase_tag = phase
            phase = "write"
        new = f"s{i}"
        label_map[sess["session"]] = new
        turns: list[dict] = []
        for ti, turn in enumerate(sess["turns"], start=1):
            if turn.get("speaker") != "user" or set(turn) - {"id", "speaker", "text", "note"}:
                raise Reject("turn_metadata_unmapped")
            if turn.get("id") != f"{sess['session']}.t{ti}":
                raise Reject("turn_id_inconsistent")
            row = {"text": turn["text"]}
            if "note" in turn:
                row["note"] = turn["note"]
            turns.append(row)
        out = {"session": new, "phase": phase, "turns": turns}
        if sess.get("time") is not None:
            out["time"] = sess["time"]
        note = sess.get("note")
        if card_phase_tag:
            note = ((note + " | ") if note else "") + "[card-meta phase=" + card_phase_tag + "]"
            relocated.append(dict(card_id=card["id"], card_session=sess["session"],
                                  paper_session=new, keys=["phase"],
                                  detail="update→write（试卷枚举无 update；原文本与时序未动）",
                                  note=note))
        if extra:
            tag = "; ".join("%s=%s" % (k, sess[k]) for k in sorted(extra))
            note = ((note + " | ") if note else "") + "[card-meta " + tag + "]"
            relocated.append(dict(card_id=card["id"], card_session=sess["session"],
                                  paper_session=new, keys=sorted(extra), note=note))
        if note:
            out["note"] = note
        rows.append(out)
    return rows, label_map, relocated


def probe_target(card: dict, label_map: dict[str, str]) -> tuple[str, int]:
    """reply_final 的 (session, turn) = phase=probe 会话的最后一轮；没有 probe 就拒，不造轮次。"""
    for sess in reversed(card["script"]):
        if sess["phase"] == "probe":
            return label_map[sess["session"]], len(sess["turns"])
    raise Reject("no_probe_session")


def card_time_codes(card: dict, args) -> list[str]:
    ts = card["time_semantics"]
    codes: list[str] = []
    if ts["mode"] not in args.allow_time_mode:
        codes.append("time_semantics_unsupported")
    wait = ts.get("minimum_real_elapsed_seconds")
    if not args.include_long_wait and isinstance(wait, int) and wait > args.max_real_wait_seconds:
        codes.append("long_real_wait")
    if ts.get("requires_process_restart") and not args.allow_restart:
        codes.append("requires_process_restart")
    return codes


def split_reason(card: dict, req: dict, path: str) -> str:
    return ("compiled from card " + card["id"] + " requirement " + req["id"] +
            " (scope=" + req["scope"] + ", path=" + path + "): existence is objective, content "
            "is semantic. One card requirement declaring N files is split per file, which the "
            "card authorizes via objective_compile_plan."
            "artifact_requirements_split_per_file_before_runtime_plan. No keyword regex was "
            "authored (fabrication forbidden), so this file has no legacy content anchor.")


# ── 混淆判据派生（v1.0-dev.2；--error-control-checks 打开，默认关 ⇒ dev.1 四份产物逐字节不变）──
# 为什么需要它（现场量出来的缺陷，不是猜测）：交付卷 75/100 题由候选卡编译而来，卡片包明令
# 禁止「字符串出现即判对/判错」，所以这些题的 legacy 锚点只能是**结构锚点**（记忆面非空 /
# 探测轮有非空回复），on_violation 一律取卡片 proposed_failure（实测全是 omitted）。后果：
# 按预注册 §3.1「hard+on_violation=confused 的锚点数」计数，memory_recall 全维只有 1 条混淆
# 判据 —— 真跑之后该维 confused 计数几乎必然是 0，而那是**我们没测**，不是智能体没犯。
# 判类的实际执行者不是锚点，是四层计划：tools/scoring/four_layer.py 里
# `row['verdict'] = failed[0]['on_failure']`。所以混淆判据要补在计划层。
# 材料只用卡片自己写好的两样，一个新关键词都不造：
#   ① card['authored_error_control']['example'] —— 出题人登记的错误对照（实测 100/100 张卡都有）；
#   ② card['allowed_equivalents'] —— 卡片自带的等价与豁免口径（原样引号内嵌进判据文本）。
# 只加**一条 semantic 检查**：题面、脚本、锚点、试卷 yaml 一个字节都不改。试卷
# expect.confusions 由锚点派生（见 compile_card），改它会撞 E120「声明可达性」——
# 结构锚点没有 on_violation=confused 可承接；所以 dev.2 的可判形态以计划的 on_failure 集合为准，
# 与试卷 expect.confusions 的差异逐题记在覆盖矩阵里（不悄悄改卷，也不假装两者一致）。
CONTENT_ERROR_CLASSES = frozenset({"confused", "wrongly_persisted", "wrongly_reused"})

ERROR_CONTROL_CARRIER = {"agent_reply": "回复", "file_final": "产物",
                         "memory_after": "记忆面", "memory_before": "记忆面"}

ERROR_CONTROL_REQUIREMENT = (
    "混淆判据（机械派生自卡片 authored_error_control；题面、脚本、锚点均未改）："
    "观察到的{carrier}在语义上落入出题人登记的错误对照『{example}』即判本条违反。"
    "只在给出了错误内容时才算违反：若只是没有给出相关内容（遗漏），由本样本其他检查判 "
    "omitted，本条不判违反。字面字符串命中不作为判据；卡片 allowed_equivalents 全部适用："
    "{equivalents}")

# 派生资格：三条全是机械判定，逐条记进矩阵（不合格的原因也记）；不做任何关键词/语义猜测。
ERROR_CONTROL_RULES = (
    ("has_error_exemplar",
     "card.authored_error_control.example 是非空字符串 —— 出题人自己登记的错误对照，"
     "派生只用它，不另造错值"),
    ("no_content_error_class_declared",
     "该卡没有任何 requirement 的 proposed_failure 属于 {confused, wrongly_persisted, "
     "wrongly_reused} —— 卡片自己的错误控制已经给了内容错类别时，不重复派生、也不改它的定类"),
    ("not_a_boundary_card",
     "card.boundary is None —— 边界识别维按 CONTRACT §11 的 withhold/release 特殊口径统计，"
     "其内容错类别是 wrongly_persisted / wrongly_reused；把它们改标 confused 会污染该维统计"),
)

# 载体优先级（固定、机械）：混淆在哪里可观察 —— 探测轮回复 > 产物 > 记忆面
ERROR_CONTROL_CARRIER_ORDER = ("agent_reply", "file_final", "memory_after", "memory_before")


def error_control_eligibility(card: dict) -> tuple[bool, dict]:
    """三条资格逐条判定；返回 (是否合格, {规则名: bool})。"""
    ec = card.get("authored_error_control") or {}
    example = ec.get("example")
    declared = {r.get("proposed_failure") for r in (card.get("requirements") or [])}
    verdict = {
        "has_error_exemplar": isinstance(example, str) and bool(example.strip()),
        "no_content_error_class_declared": not (declared & CONTENT_ERROR_CLASSES),
        "not_a_boundary_card": card.get("boundary") is None,
    }
    return all(verdict.values()), verdict


def attach_error_control_check(card: dict, checks: list, disposition: dict) -> dict:
    """给一张卡派生 1 条 on_failure=confused 的语义检查；就地改 checks / disposition。

    返回派生台账一条（进 notes/*-coverage-matrix.json，逐条可核）。
    插入位置 = 载体检查**之前**：CONTRACT §5 规定多违规时取 anchors[]/checks[] 里第一条命中，
    作者有责任把更具体的失败形态放前面 ——「答成了别的值」比「没答」更具体，
    否则张冠李戴会被同一条要求上的 omitted 吃掉，confused 永远数不到。
    """
    eligible, reasons = error_control_eligibility(card)
    record: dict = {"card_id": card["id"], "dimension": card["dimension"],
                    "eligible": eligible, "eligibility": reasons, "derived": False}
    if not eligible:
        record["skip_reason"] = "ineligible:" + ",".join(
            sorted(k for k, v in reasons.items() if not v))
        return record
    shadow = None
    for scope in ERROR_CONTROL_CARRIER_ORDER:
        shadow = next((c for c in checks
                       if c.get("layer") == "semantic" and c.get("scope") == scope), None)
        if shadow is not None:
            break
    if shadow is None:
        record["skip_reason"] = "no_semantic_carrier_check"
        return record
    holder = next((aid for aid, d in disposition.items() if shadow["id"] in d["checks"]), None)
    if holder is None:
        # validate_plan 要求每条检查都能被某个锚点溯源；找不到载体锚点就不是可派生的位置
        record["skip_reason"] = "carrier_check_not_anchor_traceable"
        return record
    ec = card["authored_error_control"]
    carrier = ERROR_CONTROL_CARRIER[shadow["scope"]]
    if shadow["scope"] == "file_final":
        carrier = carrier + " " + shadow["path"]
    text = ERROR_CONTROL_REQUIREMENT.format(
        carrier=carrier, example=ec["example"].strip(),
        equivalents=" ".join(card.get("allowed_equivalents") or []))
    derived: dict = {}
    for k, v in shadow.items():      # 逐字段照搬载体检查，只改 3 个值（键序不变 ⇒ 字节可复现）
        if k == "id":
            derived["id"] = v + "_CONF"
        elif k == "requirement":
            derived["requirement"] = text
        elif k == "on_failure":
            derived["on_failure"] = "confused"
        else:
            derived[k] = v
    idx = next(i for i, c in enumerate(checks) if c is shadow)
    checks.insert(idx, derived)
    disposition[holder]["checks"].append(derived["id"])
    disposition[holder]["reason"] += (
        "; +" + derived["id"] + " is a mechanical confused-class check derived from this card's own "
        "authored_error_control.example (no keyword authored; the card's allowed_equivalents are "
        "quoted verbatim into the requirement text)")
    record.update(derived=True, check_id=derived["id"], shadow_check_id=shadow["id"],
                  scope=shadow["scope"], anchor_carrier=holder,
                  error_exemplar=ec["example"].strip(),
                  exemplar_synthetic=ec.get("synthetic"),
                  exemplar_human_truth_label=ec.get("human_truth_label"))
    return record


def compile_card(card: dict, args) -> dict:
    """一张卡 → {sample, row, requirement_gaps, ...}；任何一个闸不过就抛 Reject。"""
    codes = list(card_time_codes(card, args))
    if card["initial_state"]["fixture_files"]:
        codes.append("fixture_files_unsupported")
    if args.require_human_review and any(v is None for v in card["review"].values()):
        codes.append("human_review_pending")
    if codes:
        raise Reject(*codes)

    rows, label_map, relocated_meta = to_session_rows(card, args)
    if len(rows) == 1 and not any(r["phase"] == "disturb" for r in rows):
        raise Reject("difficulty_undecidable")

    anchors: list[dict] = []
    checks: list[dict] = []
    disposition: dict[str, dict] = {}
    gaps: list[dict] = []
    used_paths: list[str] = []
    unsafe: list[str] = []
    struct: dict[str, list[str]] = {"memory": [], "reply": []}

    for req in card["requirements"]:
        scope, rid = req["scope"], req["id"]
        basis = join_basis(req["basis"], label_map)
        if scope in SCOPE_ARTIFACT:
            paths = list(req.get("paths") or [])
            if not paths:
                gaps.append({"requirement_id": rid, "scope": scope, "reason": "no_artifact_path",
                             "target_layer": None, "basis": basis})
                continue
            for p in paths:
                if not valid_path(p):
                    unsafe.append(p)
                    continue
                aid = f"A{len(anchors) + 1}"
                anchors.append({"id": aid, "type": "file_content", "path": p, "expect": "present",
                                "severity": "hard", "on_violation": req["proposed_failure"],
                                "desc": req["requirement"]})
                obj = {"id": aid + "_exists", "layer": "objective", "op": "file_exists", "path": p,
                       "on_failure": req["proposed_failure"], "basis": basis}
                sem = {"id": aid + "_content", "layer": "semantic", "scope": "file_final",
                       "path": p, "requirement": req["requirement"],
                       "on_failure": req["proposed_failure"], "basis": basis}
                checks += [obj, sem]
                disposition[aid] = {"role": "mixed", "checks": [obj["id"], sem["id"]],
                                    "reason": split_reason(card, req, p)}
                used_paths.append(p)
            continue

        if scope in SCOPE_MEMORY:
            target: dict = {"layer": "semantic", "scope": scope}
        elif scope == "reply_final":
            sess, turn = probe_target(card, label_map)
            target = {"layer": "semantic", "scope": "agent_reply", "session": sess, "turn": turn}
        elif REPLY_AT.match(scope):
            m = REPLY_AT.match(scope)
            old, turn = m.group(1), int(m.group(2))
            target = {"layer": "semantic", "scope": "agent_reply",
                      "session": label_map.get(old, old), "turn": turn,
                      "card_pointer_exists": any(
                          r["session"] == old and turn <= len(r["turns"]) for r in card["script"]),
                      "paper_anchor_turn_allowed": any(
                          r["session"] == label_map.get(old, old) and r["phase"] == "probe"
                          for r in rows)}
        else:
            raise Reject("scope_unmapped")
        carried = False
        if args.structural_anchors and scope == "memory_after":
            check = {"id": f"R_{rid}", "layer": "semantic", "scope": "memory_after",
                     "requirement": req["requirement"], "on_failure": req["proposed_failure"],
                     "basis": basis}
            checks.append(check)
            struct["memory"].append(check["id"])
            carried = True
        elif args.structural_anchors and target.get("scope") == "agent_reply" \
                and target.get("card_pointer_exists", True):
            turn_ref = target.pop("card_pointer_exists", None)
            allowed = target.pop("paper_anchor_turn_allowed", True)
            check = {"id": f"R_{rid}", "layer": "semantic",
                     **{k: v for k, v in target.items() if k in ("scope", "session", "turn")},
                     "requirement": req["requirement"], "on_failure": req["proposed_failure"],
                     "basis": basis}
            checks.append(check)
            struct["reply"].append(check["id"])
            carried = True
            if not allowed:
                gaps.append({"requirement_id": rid, "scope": scope,
                             "reason": "reply_turn_not_probe_session", "target_layer": check,
                             "basis": basis,
                             "detail": "检查项指向非 probe 会话轮次：四层允许（E107 只约束 legacy 锚点），"
                                       "锚点侧改用探测轮结构锚点承担，逐条留痕"})
        if not carried:
            gaps.append({"requirement_id": rid, "scope": scope,
                         "reason": "no_anchor_carrier" if args.structural_anchors
                                   else "structural_anchors_disabled",
                         "target_layer": target, "basis": basis,
                         "detail": ("the four-layer scope itself is legal, but the frozen paper schema "
                                    "requires key/key_regex (E116) or turn+regex on the matching legacy "
                                    "anchor, and this card package supplies none and forbids keyword "
                                    "scoring; no regex is invented")})

    if unsafe:
        raise Reject("artifact_path_unsafe")
    if struct["memory"]:
        first = next(r for r in card["requirements"]
                     if r["scope"] == "memory_after" and f"R_{r['id']}" in struct["memory"])
        aid = f"A{len(anchors) + 1}"
        anchors.append({"id": aid, "type": "memory_entry", "key_regex": ".*",
                        "expect": "present", "severity": "hard",
                        "on_violation": first["proposed_failure"], "desc": STRUCTURAL_MEMORY_DESC})
        # validate_plan 两条硬规则同时成立：diagnostic 位不得挂检查项，且每个检查项都必须
        # 能被某个锚点溯源。因此结构锚点记为 semantic（它守卫的就是语义层要求），reason 里
        # 写死"只断言记忆面非空、不主张任何关键词"：零记忆仍可机械判 omitted，内容对错由
        # 各要求的语义检查承担。
        disposition[aid] = {"role": "semantic", "checks": list(struct["memory"]),
                            "reason": "STRUCTURAL anchor: asserts only that a non-empty persistent-"
                                      "memory observation exists (no keyword asserted; forbids "
                                      "'string appears = correct'); it is the traceability carrier "
                                      "for " + str(len(struct["memory"])) + " semantic requirement "
                                      "checks whose text comes from the card verbatim"}
    if struct["reply"]:
        first = next(r for r in card["requirements"]
                     if r["scope"] == "reply_final" and f"R_{r['id']}" in struct["reply"])
        sess, turn = probe_target(card, label_map)
        aid = f"A{len(anchors) + 1}"
        anchors.append({"id": aid, "type": "reply_text", "turn": f"{sess}.t{turn}", "regex": ".+",
                        "expect": "present", "severity": "hard",
                        "on_violation": first["proposed_failure"], "desc": STRUCTURAL_REPLY_DESC})
        disposition[aid] = {"role": "semantic", "checks": list(struct["reply"]),
                            "reason": "STRUCTURAL anchor: asserts only that the probe turn produced "
                                      "a non-empty reply (no keyword asserted); traceability carrier "
                                      "for " + str(len(struct["reply"])) + " reply requirement "
                                      "checks whose text comes from the card verbatim"}
    if not anchors:
        raise Reject("no_artifact_path")

    # dev.2：混淆判据派生（默认关；打开后试卷 yaml/jsonl 仍逐字节不变，只有四层计划多检查项）
    error_control = (attach_error_control_check(card, checks, disposition)
                     if getattr(args, "error_control_checks", False) else None)

    expect: dict = {
        "verdict": "remembered",
        "confusions": sorted({a["on_violation"] for a in anchors}),
        "gold_evidence": [map_ptr(x, label_map) for x in card["source"]["task_and_fact_source_rounds"]],
        "notes": ("compiled by tools/freeze_candidates.py from cards-r3 card " + card["id"] +
                  "; card human_truth_label is null so verdict=remembered is the mechanical "
                  "'no declared failure anchor triggered', not a human answer key. "
                  "Requirements not carried as mandatory checks: " +
                  ", ".join(g["requirement_id"] + "(" + g["reason"] + ")" for g in gaps)),
    }
    if card["dimension"] == "boundary_recognition":
        if card["boundary"] not in ("withhold", "release"):
            raise Reject("boundary_kind_missing")
        expect["boundary_kind"] = card["boundary"]

    sample = {
        "id": card["id"], "dimension": card["dimension"], "difficulty": "L3",
        "title": card["title"], "script": rows, "anchors": anchors, "expect": expect,
        "score_policy": "hybrid",
        "meta": {
            "author": "tools/freeze_candidates.py:" + str(card["author"]["role"]),
            "created": card["_package_date"],
            "source": "adapted_from:cards-r3/" + card["id"],
            "limitation": ("candidate card is AI-authored and not human-reviewed (review.* all "
                           "null), runnable=false, frozen=false, split=development; 0 fixture "
                           "files so objective checks can only be file_exists; anchors assert "
                           "artifact existence only, the card's memory/reply requirements are "
                           "not carried as mandatory checks (see "
                           "notes/paper-r1-coverage-matrix.json)"),
        },
    }
    declared = sorted(card["objective_compile_plan"]["file_existence"])
    return {"card_id": card["id"], "dimension": card["dimension"],
           "family": card["family"]["id"], "sample": sample,
           "row": {"sample_id": card["id"], "anchor_disposition": disposition, "checks": checks},
           "requirement_gaps": gaps, "artifact_paths": used_paths,
           "session_label_map": label_map, "relocated_session_metadata": relocated_meta,
           "phases": [r["phase"] for r in rows],
           "file_existence_matches_declared_paths": declared == sorted(used_paths),
           "objective_ops": sorted({c["op"] for c in checks if c["layer"] == "objective"}),
           "scopes": sorted({c["scope"] for c in checks if c["layer"] == "semantic"}),
           "error_control": error_control}


def select(compiled: list[dict], args) -> tuple[list[dict], list[dict]]:
    """闸序末三段：family → 维度配额 → 总量。排序键=(六维枚举序, 卡片 id)，与任何 Agent 得分无关。"""
    order = {d: i for i, d in enumerate(DIMENSIONS)}
    ranked = sorted(compiled, key=lambda m: (order.get(m["dimension"], len(order)), m["card_id"]))
    kept: list[dict] = []
    capped: list[dict] = []
    per_family: collections.Counter = collections.Counter()
    per_dim: collections.Counter = collections.Counter()
    for m in ranked:
        if args.max_per_family and per_family[m["family"]] >= args.max_per_family:
            capped.append(dict(card_id=m["card_id"], dimension=m["dimension"], family=m["family"],
                               primary_reason="duplicate_family_unresolved",
                               all_reasons=["duplicate_family_unresolved"]))
            continue
        if args.per_dimension and per_dim[m["dimension"]] >= args.per_dimension:
            capped.append(dict(card_id=m["card_id"], dimension=m["dimension"], family=m["family"],
                               primary_reason="dimension_cap", all_reasons=["dimension_cap"]))
            continue
        if args.count and len(kept) >= args.count:
            capped.append(dict(card_id=m["card_id"], dimension=m["dimension"], family=m["family"],
                               primary_reason="selection_cap", all_reasons=["selection_cap"]))
            continue
        per_family[m["family"]] += 1
        per_dim[m["dimension"]] += 1
        kept.append(m)
    return kept, capped

