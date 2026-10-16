"""数据结构与常量（锚点求值四态 / task 五态 / verdict 五分类 / §7 数值映射）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 锚点级四态（§5）
R_PASS, R_VIOLATED, R_SKIP, R_ENV = "pass", "violated", "skip", "env_error"

# task 五态（§7 I-9）
STATUSES = {"ok", "timeout", "agent_error", "env_error", "skip"}

# verdict 五分类（§11）
VERDICTS = {"remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"}

EVENT_TYPES = {"memory_read", "memory_write", "tool_call"}

# 锚点级 skip 的原因枚举（自守护 #7：出现枚举外值 = 实现 bug）
SKIP_REASONS = {
    "empty_memory",          # §5 空集：absent 在 0 条目快照上不可判
    "no_event_collection",   # §15 #7：事件类行集为空 / 非事件级采集
    "truncated_content",     # §12：truncated=true 且未命中
    "snapshot_before_file",  # 契约疑点 Q4：引擎不落前置文件清单
    "probe_unverified",      # G1（ISS-13 伴生）：真实 Agent 记忆探针全 run 未采到非空 →
                             # 无法把「采集读不到」与「真没记」分开，记忆锚点不判给不判罚
}


def numeric_score(verdict: Optional[str]) -> Optional[float]:
    """§7 数值映射：remembered=1，其余四类=0，None（不计分）=None。"""
    if verdict is None:
        return None
    return 1.0 if verdict == "remembered" else 0.0


@dataclass
class AnchorOutcome:
    anchor_id: str
    atype: str
    expect: str
    severity: str
    result: str                            # R_* 之一
    hit_count: Optional[int] = None        # skip/env_error 时为 None
    evidence_refs: list = field(default_factory=list)   # 参与判定的行 id
    scan_scope: list = field(default_factory=list)      # absent 必非空（§5 铁律 2）
    on_violation: Optional[str] = None
    skip_reason: Optional[str] = None      # ∈ SKIP_REASONS
    detail: str = ""

    def tag(self) -> str:
        n = "" if self.hit_count is None else f",hit={self.hit_count}"
        ev = ",".join(self.evidence_refs[:3])
        return f"{self.anchor_id}:{self.result}{n}[{self.skip_reason or ''}](ev={ev})"


class EnvError(Exception):
    """谓词不可解析 / 证据链断裂（§5 铁律 3：不判负也不判正）。"""


class InputError(Exception):
    """run 目录 / paper 不可用（CLI exit 2）。"""
