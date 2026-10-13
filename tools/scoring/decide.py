"""定类流程（oracle §2.5 十步 + metis 自守护 #4/#5）。短路顺序即优先级，不可换。"""
from __future__ import annotations

from typing import Optional

from .anchors import evaluate
from .model import (R_ENV, R_PASS, R_SKIP, R_VIOLATED, AnchorOutcome, EnvError, VERDICTS,
                    numeric_score)


class TaskScore:
    __slots__ = ("task_id", "sample_id", "dimension", "status", "verdict", "score",
                 "score_policy", "decided_by", "evidence_refs", "judge_version", "judge_model",
                 "cache_key", "judge_error", "reason", "skipped_anchors",
                 "hard_evaluated", "hard_violated", "hard_skipped")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _null_row(sample, task_id, status, reason, refs):
    return TaskScore(task_id=task_id, sample_id=sample["id"], dimension=sample["dimension"],
                     status=status, verdict=None, score=None,
                     score_policy=sample.get("score_policy", "hybrid"), decided_by="rule",
                     evidence_refs=list(refs), judge_version=None, judge_model=None,
                     cache_key=None, judge_error=None, reason=reason, skipped_anchors=[],
                     hard_evaluated=[], hard_violated=[], hard_skipped=[])


def precheck(sample, idx, task_id, summary) -> Optional[str]:
    """完整性检查（E1/E7）。返回 env_error 原因或 None。"""
    rows = idx.task_rows(task_id)
    if not rows:
        return "E1: 该 task 无任何证据行"
    if summary is not None and summary["payload"].get("status") == "ok":
        want = idx.total_turns(sample)
        got = len(idx.agent_messages(task_id))
        if want != got:
            return f"E7: agent 回复行 {got} ≠ 样本轮次 {want}（对话链路不完整，absent 负断言不可信）"
    return None


def decide_task(sample, idx, task_id, judge=None) -> TaskScore:
    summary = idx.system_action(task_id, "task_summary")
    skiprow = idx.system_action(task_id, "skip")
    if summary is None and skiprow is None:
        refs = [idx.task_rows(task_id)[0]["id"]] if idx.task_rows(task_id) else []
        return _null_row(sample, task_id, "env_error", "E1: 缺 task_summary 与 skip 证据", refs)
    row = summary or skiprow
    status = row["payload"].get("status") or ("skip" if skiprow else "env_error")
    sreason = row["payload"].get("reason") or ""
    base_refs = [row["id"]]

    # 步骤 1：status != ok → 不评估锚点（证据链不完整时评估=假分）
    if status != "ok":
        return _null_row(sample, task_id, status, f"task 状态 {status}：{sreason}", base_refs)

    # 步骤 2：完整性
    bad = precheck(sample, idx, task_id, summary)
    if bad:
        return _null_row(sample, task_id, "env_error", bad, base_refs)

    # 步骤 3：逐锚点求值
    anchors = sample.get("anchors", [])
    outcomes: list[AnchorOutcome] = []
    try:
        for a in anchors:
            outcomes.append(evaluate(sample, a, idx, task_id))
    except EnvError as exc:
        ts = _null_row(sample, task_id, "env_error", f"E: 谓词不可求值 → {exc}", base_refs)
        ts.evidence_refs = base_refs + [o.anchor_id and rid for o in outcomes for rid in o.evidence_refs[:1]]
        return ts

    hard = [o for o in outcomes if o.severity == "hard"]
    hard_evaluated = [o.anchor_id for o in hard if o.result in (R_PASS, R_VIOLATED)]
    hard_violated = [o.anchor_id for o in hard if o.result == R_VIOLATED]
    hard_skipped = [o.anchor_id for o in hard if o.result == R_SKIP]
    hard_present_ids = {str(a.get("id")) for a in anchors
                        if a.get("severity") == "hard" and a.get("expect") == "present"}
    skipped_ids = [o.anchor_id for o in outcomes if o.result == R_SKIP]

    def collect_refs():
        seen, out = set(), []
        for o in outcomes:
            for rid in list(o.evidence_refs) + list(o.scan_scope):
                if rid not in seen:
                    seen.add(rid)
                    out.append(rid)
        return base_refs + [r for r in out if r not in seen]

    refs = collect_refs()
    policy = sample.get("score_policy", "hybrid")

    # 步骤 4：hard+present 门槛全部被 skip → task 级 skip（Q10 裁决 a：取「全部」读法）
    present_hard = [o for o in hard if o.anchor_id in hard_present_ids]
    if present_hard and all(o.result == R_SKIP for o in present_hard):
        ts = _null_row(sample, task_id, "skip",
                       "§13 #8：唯一/全部 hard+present 正向门槛被 skip（缺采集不得给分）："
                       + "、".join(f"{o.anchor_id}({o.skip_reason})" for o in present_hard), refs)
        ts.skipped_anchors = skipped_ids
        ts.hard_skipped = hard_skipped
        return ts

    # 步骤 5：多违规取声明序首条 hard 违反
    first_v = next((o for o in outcomes if o.severity == "hard" and o.result == R_VIOLATED), None)
    if first_v is not None:
        verdict = first_v.on_violation
        if verdict not in VERDICTS:
            return _null_row(sample, task_id, "env_error", f"定类锚点 {first_v.anchor_id} 缺 on_violation", refs)
        others = "；".join(o.tag() for o in outcomes
                           if o is not first_v and o.result in (R_VIOLATED, R_SKIP))
        return TaskScore(task_id=task_id, sample_id=sample["id"], dimension=sample["dimension"],
                         status="ok", verdict=verdict, score=numeric_score(verdict),
                         score_policy=policy, decided_by="rule", evidence_refs=refs,
                         judge_version=None, judge_model=None, cache_key=None, judge_error=None,
                         reason=f"rule:{verdict} via {first_v.tag()}" + (f" | 其余: {others}" if others else ""),
                         skipped_anchors=skipped_ids, hard_evaluated=hard_evaluated,
                         hard_violated=hard_violated, hard_skipped=hard_skipped)

    # 步骤 6：全部 hard 通过 → expect.verdict 正确类；但 guard #5（metis R3 的洞）：
    # remembered 且有 hard 锚点被 skip → 规则路不可信，整行降 skip+证据
    verdict = sample.get("expect", {}).get("verdict") or "remembered"
    if hard_skipped and verdict == "remembered":
        det = "、".join(f"{o.anchor_id}({o.skip_reason})" for o in hard if o.result == R_SKIP)
        ts = _null_row(sample, task_id, "skip",
                       f"guard#5：全 hard 通过但有 hard 锚点不可判，remembered 不成立：{det}", refs)
        ts.skipped_anchors = skipped_ids
        ts.hard_skipped = hard_skipped
        return ts

    # 步骤 8：soft 交判官（NullJudge → 保持规则结论）
    soft = [o for o in outcomes if o.severity == "R_SOFT" or o.severity == "soft"]
    decision = None
    judge_error = None
    jd = judge or _NULL_JUDGE
    if soft and policy in ("hybrid", "judge_only"):
        try:
            decision = jd.judge_soft(sample, soft, idx, task_id)
        except Exception as exc:  # noqa: BLE001 —— 判官失败：§13 #5 ②
            judge_error = f"{type(exc).__name__}: {exc}"
    if judge_error is not None:
        ts = _null_row(sample, task_id, "ok", f"judge_error: {judge_error}", refs)
        ts.judge_error = judge_error
        ts.decided_by = "judge"
        ts.skipped_anchors = skipped_ids
        return ts

    if decision is not None:
        verdict = decision.verdict
        return TaskScore(task_id=task_id, sample_id=sample["id"], dimension=sample["dimension"],
                         status="ok", verdict=verdict, score=numeric_score(verdict),
                         score_policy=policy, decided_by="judge", evidence_refs=refs,
                         judge_version=decision.version, judge_model=decision.model,
                         cache_key=decision.cache_key, judge_error=None,
                         reason=f"judge:{verdict} via {first_v.tag() if first_v else 'soft'} | "
                                + " ".join(o.tag() for o in soft),
                         skipped_anchors=skipped_ids, hard_evaluated=hard_evaluated,
                         hard_violated=hard_violated, hard_skipped=hard_skipped)

    # 步骤 6/7：规则结论成立
    return TaskScore(task_id=task_id, sample_id=sample["id"], dimension=sample["dimension"],
                     status="ok", verdict=verdict, score=numeric_score(verdict),
                     score_policy=policy, decided_by="rule", evidence_refs=refs,
                     judge_version=None, judge_model=None, cache_key=None, judge_error=None,
                     reason="rule:" + verdict + " | " + " ".join(o.tag() for o in outcomes),
                     skipped_anchors=skipped_ids, hard_evaluated=hard_evaluated,
                     hard_violated=hard_violated, hard_skipped=hard_skipped)


class _NullJudgeImpl:
    def judge_soft(self, sample, soft_outcomes, idx, task_id):
        return None  # 判官未上线：规则结论成立，judge_* 留 null（不得借 judge_error 表达「没跑」）


_NULL_JUDGE = _NullJudgeImpl()
