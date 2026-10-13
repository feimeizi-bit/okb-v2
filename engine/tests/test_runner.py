"""engine.runner 纯函数单测（任务展开 / 状态汇总 / 能力预判）

用法：python engine/tests/test_runner.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.adapter import Capabilities  # noqa: E402
from engine.runner import (  # noqa: E402
    check_capabilities,
    derive_requires,
    duplicate_agent_names,
    plan_tasks,
    summarize,
)

SAMPLE = {
    "id": "s1",
    "time_anchor": "2026-05-20T10:00:00+08:00",
    "fixture": [{"path": "a.txt", "content": "x"}],
    "anchors": [{"type": "memory_entry"}, {"type": "tool_call"}, {"type": "reply_text"}],
}


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # plan_tasks
    samples = [{"id": "a"}, {"id": "b"}]
    plan = plan_tasks(["openclaw", "hermes"], samples, repeat=2)
    check("计划总数", len(plan) == 8)
    check("顺序：agent 外层", plan[0]["agent"] == "openclaw" and plan[-1]["agent"] == "hermes")
    check("任务 id 带重复号", plan[0]["task_id"] == "openclaw-a-r1" and plan[1]["task_id"] == "openclaw-a-r2")
    check("repeat=1 不带后缀", plan_tasks(["openclaw"], [{"id": "a"}], 1)[0]["task_id"] == "openclaw-a")

    # ISS-11：同名 agent 检出（CLI 与 run_batch 双层守卫共用的纯函数）
    check("重复 agent 检出", duplicate_agent_names(["mock", "openclaw", "mock"]) == ["mock"])
    check("重复 agent 多值", duplicate_agent_names(["a", "b", "a", "b", "a"]) == ["a", "b"])
    check("无重复为空", duplicate_agent_names(["openclaw", "hermes", "mock"]) == [])

    # summarize
    summary = summarize(
        [
            {"status": "ok"},
            {"status": "ok"},
            {"status": "skip"},
            {"status": "timeout"},
            {"status": "agent_error"},
            {"status": "env_error"},
        ]
    )
    check("汇总总数", summary["total"] == 6)
    check("汇总 ok", summary["by_status"].get("ok") == 2)
    check("汇总 invalid", summary["invalid"] == 3)
    check("汇总 skip", summary["skipped"] == 1)

    # 能力预判
    check("requires 派生", derive_requires(SAMPLE) == {"time_anchor", "file", "memory", "tool"})
    caps = Capabilities(dialogue=True, memory=True, file=True, tool=False, supports_time_anchor=False)
    missing = check_capabilities(SAMPLE, caps)
    check("缺 time_anchor 与 tool", len(missing) == 2 and any("time_anchor" in m for m in missing) and "tool" in missing)
    full = Capabilities(dialogue=True, memory=True, file=True, tool=True, supports_time_anchor=True)
    check("全能力无缺失", check_capabilities(SAMPLE, full) == [])

    if failures:
        print(f"❌ runner 测试未通过：{failures}")
        return 1
    print("✅ runner 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
