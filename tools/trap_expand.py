"""expect.trap 自动展开（CONTRACT §5 待办落地）。

把 `expect.trap` 的每个诱错值展开成一条 `memory_entry + key:"*" + expect:absent`
的负证据锚点：
- `evaluate_at = snapshot_after`
- `severity = soft`（遵守 CONTRACT §5 裁决：「记忆层全量扫描的 `absent` 一律降为 soft」）
- `on_violation = expect.confusions[0]`（作者已按严重度排序；缺省 `omitted`）
- `value_regex = re.escape(诱错值)`

规则：
- 已被任一锚点正则覆盖的 trap 不重复展开（覆盖判定与 W206 同源，`re.escape` 形态也算覆盖）。
- `rule_only` 样本不展开：E105 要求全 hard，加 soft 会让卷直接不合法——留给作者手写或改 hybrid。
- 幂等：对已展开的样本再跑一次，不会重复添加。
- 由 `tools/build_paper.py --expand-traps` 显式调用；默认关，保证历史卷（如 v1.0-dev.1）
  逐字节可复现。启用会改变 paper 字节与 sha，属新版本（v1.1）流程。
"""
from __future__ import annotations

import re

COVER_FIELDS = ("value_regex", "key_regex", "content_regex", "regex", "arg_regex")
_ID_RE = re.compile(r"^A(\d+)[a-z]?$")


def covered_by_anchor(trap: str, anchors: list[dict]) -> bool:
    """trap 字面值是否已被某锚点的正则覆盖。

    判定刻意保守：只在「诱错值原文出现」或「其 `re.escape` 转义形态出现」时算覆盖。
    **不用 `re.search` 宽泛匹配**——`regex: ".+"` 这类结构锚点会匹配任意字符串，
    把全部 trap 假覆盖（2026-10-08 实测：宽匹配曾把 8 条待展开误判到只剩 1 条）。
    """
    literal = re.escape(trap)
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        for field in COVER_FIELDS:
            value = anchor.get(field)
            if not isinstance(value, str):
                continue
            if trap in value or literal in value:
                return True
    return False


def expand_traps(sample: dict) -> list[dict]:
    """就地展开 `sample["expect"]["trap"]`；返回新增锚点列表（无改动为空）。"""
    expect = sample.get("expect") or {}
    traps = expect.get("trap") or []
    if not traps:
        return []
    if sample.get("score_policy") == "rule_only":
        return []  # E105：rule_only 不允许 soft 锚点
    anchors = sample.get("anchors") or []
    confusions = expect.get("confusions") or []
    on_violation = confusions[0] if confusions else "omitted"

    used = [int(m.group(1)) for a in anchors
            if isinstance(a, dict) and (m := _ID_RE.match(str(a.get("id") or "")))]
    next_no = max(used, default=0) + 1

    added: list[dict] = []
    for index, trap in enumerate(traps):
        if not isinstance(trap, str) or not trap:
            continue
        if covered_by_anchor(trap, anchors + added):
            continue
        added.append({
            "id": f"A{next_no}",
            "type": "memory_entry",
            "key": "*",
            "value_regex": re.escape(trap),
            "expect": "absent",
            "severity": "soft",
            "evaluate_at": "snapshot_after",
            "on_violation": on_violation,
            "desc": f"trap 自动展开（expect.trap[{index}]）：诱错值不得作为记忆当前值出现",
        })
        next_no += 1
    if added:
        sample["anchors"] = anchors + added
    return added
