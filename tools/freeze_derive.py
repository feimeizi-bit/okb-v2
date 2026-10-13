#!/usr/bin/env python3
"""题卡锚点 → 四层计划检查项的机械派生（从 tools/freeze_candidates.py 拆出）。

只搬不改：派生规则、原因码、结构锚点文案与拆分前逐字一致，验收标准是
tools/tests/test_paper_r1_reproducible.py 要求交付卷四份产物逐字节不变。
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

def derive_pilot_checks(sample: dict) -> tuple[list[dict], list[dict], dict]:
    """一条 pilot 样本 → (plan checks, anchor gaps, anchor_disposition)。

    只做字段搬运与机械映射：
      memory_entry            → semantic / memory_after
      file_content present    → objective file_exists + semantic file_final（存在性客观、
                                内容正确性语义 ⇒ 拆两条，与卡片侧同一拆法）
      file_content absent     → semantic / file_final
      reply_text             → semantic / agent_reply（session/turn 由 anchor.turn 拆出）
    搬不动的就记缺口，绝不替他物做语义裁决。
    """
    checks: list[dict] = []
    gaps: list[dict] = []
    disposition: dict[str, dict] = {}
    for a in sample.get("anchors") or []:
        aid = str(a["id"])
        expect = str(a.get("expect"))
        on_v = a.get("on_violation")
        desc = str(a.get("desc") or "").strip()
        src = ("anchor " + aid + " (type=" + str(a.get("type")) + ", expect=" + expect +
               ", severity=" + str(a.get("severity")) + ", regex=" +
               str(a.get("regex") or a.get("value_regex") or a.get("content_regex") or "-") +
               ") — " + PILOT_DERIVE_NOTE)
        if a.get("type") in EVENT_ANCHOR_TYPES:
            gaps.append(dict(sample_id=sample["id"], anchor_id=aid, scope_hint=str(a.get("type")),
                             reason="event_scope_not_representable",
                             detail="事件锚点在 four_layer.SCOPES 里没有对应语义 scope，"
                                    "改判成 memory_after 就是替他物做语义裁决"))
            disposition[aid] = dict(role="diagnostic", checks=[],
                                   reason="event anchor not representable as a mandatory check "
                                          "(see pilot_anchor_gaps_by_reason)")
            continue
        if on_v is not None and on_v not in FAILURES:
            gaps.append(dict(sample_id=sample["id"], anchor_id=aid, scope_hint=str(a.get("type")),
                             reason="on_violation_not_a_failure",
                             detail="锚点 on_violation=" + str(on_v) + " 不是失败类 ⇒ 不能当 on_failure 用"))
            disposition[aid] = dict(role="diagnostic", checks=[],
                                   reason="anchor on_violation is not a failure class")
            continue
        if on_v is None or not desc:
            gaps.append(dict(sample_id=sample["id"], anchor_id=aid, scope_hint=str(a.get("type")),
                             reason="on_violation_undeclared",
                             detail="锚点没声明违反即判哪一类 ⇒ on_failure 不可新造"))
            disposition[aid] = dict(role="diagnostic", checks=[],
                                   reason="anchor declares no on_violation; kept as diagnostic only")
            continue
        made: list[str] = []
        if a.get("type") == "memory_entry":
            cid = "pilot_" + sample["id"] + "_" + aid
            # evaluate_at 是题卡 schema 里的一等字段，规则评分器（scoring/anchors.py）已经在消费
            # snapshot_before；但这里此前把 memory_entry 一律派生成 memory_after，导致"任务前基线"
            # 类断言无法进入四层评分（实测 memory_before scope = 0）。按锚点自己的判定时点派生。
            at = a.get("evaluate_at") or "snapshot_after"
            scope = "memory_before" if at == "snapshot_before" else "memory_after"
            # basis 只在**显式**声明判定时点时追加标注：默认 snapshot_after 不给每行都加尾巴，
            # 否则已交付的 four-layer-r1.json 就无法逐字节复现（实测：加尾巴后 plan sha 变了，
            # 而 RUN_ON_OPENKYLIN.md 承诺"重跑逐字节一致"）。
            basis = src if at == "snapshot_after" else src + ", evaluate_at=" + at
            checks.append(dict(id=cid, layer="semantic", scope=scope,
                              requirement=desc, on_failure=on_v, basis=basis))
            made.append(cid)
        elif a.get("type") == "file_content":
            path = str(a.get("path") or "")
            if not valid_path(path):
                gaps.append(dict(sample_id=sample["id"], anchor_id=aid, scope_hint="file_content",
                                 reason="artifact_path_unsafe", detail="path 不满足 valid_path"))
                disposition[aid] = dict(role="diagnostic", checks=[], reason="unsafe artifact path")
                continue
            if expect == "present":
                oid = "pilot_" + sample["id"] + "_" + aid + "_exists"
                checks.append(dict(id=oid, layer="objective", op="file_exists", path=path,
                                  on_failure=on_v, basis=src))
                made.append(oid)
            sid = "pilot_" + sample["id"] + "_" + aid + "_content"
            checks.append(dict(id=sid, layer="semantic", scope="file_final", path=path,
                              requirement=desc, on_failure=on_v, basis=src))
            made.append(sid)
        elif a.get("type") == "reply_text":
            turn = a.get("turn")
            if turn == "*" or not isinstance(turn, str) or ".t" not in turn:
                gaps.append(dict(sample_id=sample["id"], anchor_id=aid, scope_hint="reply_text",
                                 reason="reply_turn_wildcard",
                                 detail="agent_reply 检查必须指到确切轮次（session + turn），"
                                        "turn=\"*\" 无法机械确定"))
                disposition[aid] = dict(role="diagnostic", checks=[],
                                       reason="wildcard reply turn cannot select an exact check")
                continue
            session, _, number = turn.partition(".t")
            cid = "pilot_" + sample["id"] + "_" + aid
            checks.append(dict(id=cid, layer="semantic", scope="agent_reply", session=session,
                              turn=int(number), requirement=desc, on_failure=on_v, basis=src))
            made.append(cid)
        else:
            gaps.append(dict(sample_id=sample["id"], anchor_id=aid, scope_hint=str(a.get("type")),
                             reason="scope_unmapped", detail="未知锚点类型，不猜映射"))
            disposition[aid] = dict(role="diagnostic", checks=[], reason="unknown anchor type")
            continue
        kinds = {c["layer"] for c in checks if c["id"] in made}
        disposition[aid] = dict(role="mixed" if kinds == {"objective", "semantic"} else
                                ("objective" if kinds == {"objective"} else "semantic"),
                                checks=made, reason=src)
    return checks, gaps, disposition


def pilot_block(text: str) -> str:
    """pilot 文件 samples: 之后的全部字节 —— 原样并入用的就是这一段。"""
    m = re.search(r"(?m)^samples:[ \t]*$", text)
    if not m:
        raise SystemExit("pilot file has no top-level `samples:` line")
    return text[m.end():]


def dump_samples(samples: list[dict]) -> str:
    """编译样本 → YAML 文本块（缩进两级，与 pilot 文件同一风格；确定性输出）。"""
    chunks = []
    for s in samples:
        text = yaml.dump([s], allow_unicode=True, default_flow_style=False, sort_keys=False,
                         width=1_000_000, indent=2)
        chunks.append("".join(("  " + line) if line.strip() else line
                              for line in text.splitlines(keepends=True)))
    return "".join(chunks)


HEADER = """# samples/paper-r1.yaml —— 第一轮正式试卷（suite_version {suite}）
#
# 由 `python3 tools/freeze_candidates.py` 编译产出；映射规则、闸序、原因码全部写在
# tools/freeze_candidates.py 的模块 docstring 里。本文件可整份删除后重跑复算，
# 复算性由 tools/tests/test_freeze_candidates.py 断言（含两次重跑字节一致）。
#
# 组成（顺序固定：编译样本在前，pilot 原样并入块在最后）：
#   1) 编译自私有候选卡包 cards-r3：{compiled}/{cards_total} 张过闸，
#      {rejected} 张不可编译，逐张原因码见 notes/paper-r1-coverage-matrix.json
#      （compile_report.uncompilable_cards）。这些卡是 AI 起草、未经人工审阅
#      （review.* 全 null）、runnable=false、frozen=false 的开发集候选，每张样本的
#      meta.limitation 已就地声明 —— 本卷不是「人工已审阅」的声明；
#      用 --require-human-review 重跑即可复算出「候选卡 0 条进卷」的那一版口径。
#   2) {pilot_count} 条 samples/pilot-v0.2.yaml 样本：文本级原样并入（不重新生成、不改字段），
#      它们是本卷唯一带 fixture 与 file/tool 锚点的可执行样本 —— 候选卡 0 fixture，
#      「文件/产物证据」这一类只有靠并入的 pilot 样本才测得出来。
#      注意：build_paper.py 会把文件级 suite_version 无条件盖进每条样本的
#      meta.suite_version，所以 YAML 文本逐字节一致，而 JSONL 里 pilot 行的
#      meta.suite_version 从 v0.2 变成 {suite}（既有工具的机械派生，不是改题）。
#
# 候选卡包 sha256：{cards_sha}
# pilot-v0.2.yaml sha256：{pilot_sha}
#
# 构建：python3 tools/build_paper.py samples/paper-r1.yaml -o build/paper-r1.jsonl
# 校验：python3 -m okb validate build/paper-r1.jsonl
# 评分：python3 -m okb run --agents deploy/agents.offline.yaml --paper build/paper-r1.jsonl \\
#           --plan samples/four-layer-r1.json --run-id <id> --sample-id <全部 plan 样本 id>
suite_version: "{suite}"
samples:

"""


# 事件类锚点：four_layer.SCOPES 只有 memory_before/memory_after/agent_reply/file_final，
# 没有「读/写/工具事件」这一语义 scope ⇒ 不能伪装成 memory_after 检查（那是改判），记缺口。
EVENT_ANCHOR_TYPES = ("memory_read", "memory_write", "tool_call")


PILOT_DERIVE_NOTE = ("mechanically derived from this sample's own human-authored anchor; "
                     "requirement text and on_failure are the anchor's own fields, nothing invented")
