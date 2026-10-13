"""verdicts.jsonl 行生成与写出前自检（§13 逐字段 + 自守护断言可写化的部分）。"""
from __future__ import annotations

import json
from pathlib import Path

from .model import STATUSES, VERDICTS
from .decide import TaskScore

ROW_ORDER = ["task_id", "sample_id", "dimension", "status", "verdict", "score", "score_policy",
             "decided_by", "evidence_refs", "judge_version", "judge_model", "cache_key",
             "judge_error", "reason", "skipped_anchors",
             "hard_evaluated", "hard_violated", "hard_skipped"]  # hard_* 三元组：验收反馈 G4，
# 会签包增补项（§13 可选字段，使「skip 带证据」可机器审不必从 reason 反解）

# 定类路径产出的 verdict 必须 ∈ 该样本 anchors 声明过的 on_violation ∪ expect.verdict（自守护 #10）


def verdict_row(ts: TaskScore) -> dict:
    row = {k: getattr(ts, k) for k in ROW_ORDER}
    if ts.verdict is None:
        row["verdict"], row["score"] = None, None
    return row


def check_row(row: dict, idx) -> list[str]:
    problems = []
    st = row.get("status")
    v = row.get("verdict")
    if st not in STATUSES:
        problems.append(f"status 非法: {st}")
    # §13 #5：verdict=null 的合法情形只有两种
    if (v is None) != bool(st != "ok" or row.get("judge_error")):
        problems.append("verdict=null 条件违反 §13 #5")
    if v is not None and v not in VERDICTS:
        problems.append(f"verdict 不在五分类: {v}")
    # 自守护 #3：score 只允许 0/1/None，且 1 ⇔ remembered
    s = row.get("score")
    if s not in (0, 1, 0.0, 1.0, None):
        problems.append(f"score 出现第三种值: {s}（§7 只允许 0/1/不计）")
    if (s == 1) != (v == "remembered"):
        problems.append("score==1 与 verdict==remembered 不等价")
    if v is None and s is not None:
        problems.append("verdict=null 但 score 有值")
    # §13 #1：refs 可定位；#6：skip/env_error 必带证据
    refs = row.get("evidence_refs") or []
    if not refs:
        problems.append("evidence_refs 为空（§13 #1/#6）")
    for rid in refs:
        if rid not in idx.by_id:
            problems.append(f"evidence_ref 不可定位: {rid}")
    # §13 #7：纯规则行 judge_* 必须 null
    if row.get("decided_by") == "rule" and any(row.get(k) for k in ("judge_version", "judge_model", "cache_key")):
        problems.append("rule 行携带判官字段（§13 #7 反向违反）")
    if not row.get("reason"):
        problems.append("reason 为空（§13 恒非空）")
    return problems


def write_verdicts(path: Path, rows: list[dict]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
