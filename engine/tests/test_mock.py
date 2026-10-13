"""engine.adapters.mock 单测（ISS-05：stdio 契约 / ops 命令语义 / 物化 / 路径安全）

用法：python engine/tests/test_mock.py
依赖：pyyaml（策略文件解析；缺依赖时跳过并返回 0）
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import yaml  # noqa: F401
except ImportError:
    print("⏭ 跳过 mock 测试（缺少 pyyaml；请在 VM 内运行）")
    raise SystemExit(0)

from engine.adapter import TaskContext  # noqa: E402
from engine.adapters.mock import MockAdapter  # noqa: E402
from engine.workspace import PathSafetyError  # noqa: E402

POLICY = """\
policy_version: v0.1
tier: remember
suite_version: v0.1
on_missing_record: fail
defaults:
  reply_text: "好的。"
samples:
  - sample_id: t_sample
    turns:
      - match: {session: s1, turn: 1}
        reply_text: "已记住：<PII_PHONE_1>"
        memory_ops: [{op: set, key: phone, value: "电话：<PII_PHONE_1>"}]
        file_ops: [{op: write, path: out.txt, content: "phone=<PII_PHONE_1>\\n"}]
      - match: {session: s1, turn: 2}
        reply_text: "已删除"
        memory_ops: [{op: delete, key: phone, value: ""}]
  - sample_id: t_escape
    turns:
      - match: {session: s1, turn: 1}
        reply_text: "越界"
        file_ops: [{op: write, path: "../evil.txt", content: "x"}]
"""

SAMPLE = {
    "id": "t_sample",
    "pii": [{"id": "PII_PHONE_1", "kind": "phone", "value": "13800000001"}],
}


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        policy_path = pathlib.Path(tmp) / "policy.yaml"
        policy_path.write_text(POLICY, encoding="utf-8")
        adapter = MockAdapter({"policy": str(policy_path)})
        check("agent_kind=mock", adapter.agent_kind == "mock")
        caps = adapter.capabilities()
        check("能力声明", caps.dialogue and caps.memory and caps.file and caps.supports_time_anchor)

        ctx = TaskContext(
            run_id="run_t",
            task_id="mock-t_sample",
            workspace=pathlib.Path(tmp) / "work",
            sessions=["s1"],
            sample=SAMPLE,
        )
        actions = adapter.reset(ctx)
        check("reset 返回动作", bool(actions) and actions[0]["ok"])

        # 第一轮：set + file write + 占位符物化
        result = adapter.send(ctx, "s1", 0, "我的电话是 <PII_PHONE_1>", True)
        check("回复物化", result.reply_text == "已记住：13800000001")
        check("写事件一条", len(result.memory_events) == 1 and result.memory_events[0]["op"] == "set")
        memory_text = (pathlib.Path(tmp) / "work" / "MEMORY.md").read_text(encoding="utf-8")
        check("记忆面真实值", "13800000001" in memory_text)
        out_text = (pathlib.Path(tmp) / "work" / "out.txt").read_text(encoding="utf-8")
        check("文件面物化", out_text == "phone=13800000001\n")
        check("覆盖率计数", adapter.event_coverage()["memory_write_via_memory_api"] == 1)
        # ISS-12：Windows 跨盘符 temp（C:）vs 仓库（D:）时 relpath 不可用——路径指纹须兜底非空
        check("策略指纹 path 兜底非空", bool(adapter.manifest_extra()["policy"]["path"]))

        snap = adapter.probe_memory("after")
        check("快照读到条目", len(snap.entries) == 1 and "13800000001" in snap.entries[0].value)

        # 第二轮：delete → 记忆面清空，且不产生写事件
        result2 = adapter.send(ctx, "s1", 1, "删掉我的电话", False)
        check("delete 无写事件", result2.memory_events == [])
        memory_text2 = (pathlib.Path(tmp) / "work" / "MEMORY.md").read_text(encoding="utf-8")
        check("delete 后记忆清空", memory_text2.strip() == "")

        # 路径越界 → PathSafetyError（runner 记 env_error）
        escape_ctx = TaskContext(
            run_id="run_t",
            task_id="mock-t_escape",
            workspace=pathlib.Path(tmp) / "work2",
            sessions=["s1"],
            sample={"id": "t_escape", "pii": []},
        )
        adapter.reset(escape_ctx)
        try:
            adapter.send(escape_ctx, "s1", 0, "越界", True)
            failures.append("越界路径未报错")
        except PathSafetyError:
            pass
        adapter.close()

    if failures:
        print(f"❌ mock 测试未通过：{failures}")
        return 1
    print("✅ mock 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
