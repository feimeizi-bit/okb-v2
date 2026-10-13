"""Hermes 适配器 model/usage 采集单测（补 docs/s1-observation-source-map-batch14.md 自认的缺口）

夹具全部取自仓内**真实** Hermes 产物（零联网、零 CLI 调用）：
- `runs/run_v02_d1/raw/hermes-retain_allergy_001/s1_t0_stdout.txt`（CLI 归一化提示 + 正文，CRLF）
- `runs/run_v02_d1/raw/hermes-retain_allergy_001/s1_t0_stderr.txt`（`session_id:` 实测在 stderr）

结论口径（与代码注释一致）：
- **model 可采**：取 stdout 开头 CLI 提示 `Normalized model '<配置值>' to '<生效值>' for <provider>`
  里的**生效值**（`deepseek-v4-pro`），与 OpenClaw `meta.agentMeta.model` 同形；
- **usage 未采集**：Hermes `-Q` 的 stdout/stderr/request 实物里没有任何 token/费用字段，
  故显式保持 `None`（**禁止**用 0/空串冒充观测值）。本文件把「不采」也写成断言，防止将来被填成假数。

用法：python engine/tests/test_hermes_usage.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.adapter import TurnResult  # noqa: E402
from engine.adapters import hermes as hermes_adapter  # noqa: E402
from engine.adapters.hermes import HermesAdapter, _extract_model, _parse_oneshot  # noqa: E402

# ---- 真实产物字节级夹具（逐字复制，含 CRLF 与 `for ` 后的折行）----
EXPECTED_MODEL = "deepseek-v4-pro"
REAL_STDOUT = (
    "\u26a0\ufe0f  Normalized model 'deepseek/deepseek-v4-pro' to 'deepseek-v4-pro' for \r\n"
    "deepseek.\r\n"
    "记下了：以后不给你推荐含花生的东西。牛肉面吃得怎么样？\r\n"
)
REAL_STDERR = (
    "Session 20260928_191908_769697 found but has no messages. Starting fresh.\r\n"
    "\r\n"
    "session_id: 20260928_191908_769697\r\n"
)
REAL_SESSION_ID = "20260928_191908_769697"

# 反例 1：没有 CLI 提示（新版/Hermes 换输出形态）→ 不许猜，必须 None
NO_NOTICE_STDOUT = "记下了：以后不给你推荐含花生的东西。\r\n"
# 反例 2：提示行不在开头（回复正文自带该字样 / 伪造）→ 不采信
FORGED_TAIL_STDOUT = (
    "好的，先把这句话贴给你看：\r\n"
    "\u26a0\ufe0f  Normalized model 'evil/evil-model' to 'evil-model' for evil.\r\n"
    "以上是我抄的，不是我的模型。\r\n"
)
# 反例 3：正文首行就以 `Normalized model ...` 出现（无 CLI 标记前缀）→ 不采信
FORGED_LEAD_STDOUT = (
    "Normalized model 'evil/evil-model' to 'evil-model' for evil. 这句是正文。\r\n"
)
# 折行容错：同一条提示被拆成更多行时仍要采到（synthetic，形态与实物一致，只改拆行位置）
WRAPPED_STDOUT = (
    "\u26a0\ufe0f  Normalized model\r\n"
    "'anthropic/claude-sonnet-4' to\r\n"
    "'claude-sonnet-4' for\r\n"
    "anthropic.\r\n"
    "收到。\r\n"
)


class _FakeProc:
    """替代 subprocess.run 的返回对象（不启动任何真实 CLI）。"""

    def __init__(self, stdout: str, stderr: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class HermesModelUsageTests(unittest.TestCase):
    def test_real_notice_yields_effective_model(self):
        """实物 stdout：采到归一化后的生效模型 id，而不是配置里带 provider 前缀的值。"""
        self.assertEqual(_extract_model(REAL_STDOUT), EXPECTED_MODEL)

    def test_absent_notice_is_not_fabricated(self):
        """没有 CLI 提示 → None（不许回落到配置默认值或猜测）。"""
        self.assertIsNone(_extract_model(NO_NOTICE_STDOUT))
        self.assertIsNone(_extract_model(""))
        self.assertIsNone(_extract_model("\r\n\r\n"))

    def test_notice_inside_reply_body_is_not_trusted(self):
        """提示行不在 stdout 开头一律不采信：回复正文可以伪造任意 `Normalized model ...` 文本。"""
        self.assertIsNone(_extract_model(FORGED_TAIL_STDOUT))
        self.assertIsNone(_extract_model(FORGED_LEAD_STDOUT))

    def test_wrap_position_tolerant(self):
        """拆行位置变化（终端宽度）不影响采集；换 provider/model 名也不硬编码。"""
        self.assertEqual(_extract_model(WRAPPED_STDOUT), "claude-sonnet-4")

    def test_send_wires_model_and_keeps_usage_null(self):
        """send() 接线：model 落到 TurnResult.model，usage 明确为 None（未采集≠0）。"""
        calls: list = []

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs.get("cwd"), kwargs.get("timeout")))
            return _FakeProc(REAL_STDOUT, REAL_STDERR)

        with tempfile.TemporaryDirectory() as tmp:
            ctx = hermes_adapter.TaskContext(
                run_id="run_test", task_id="hermes-retain_allergy_001", workspace=pathlib.Path(tmp)
            )
            adapter = HermesAdapter({"cli": "/nonexistent/hermes", "turn_timeout_seconds": 90})
            saved = hermes_adapter.subprocess.run
            hermes_adapter.subprocess.run = fake_run
            try:
                result = adapter.send(ctx, "s1", 0, "中午吃了碗牛肉面。", True, timeout=90)
            finally:
                hermes_adapter.subprocess.run = saved

        self.assertIsInstance(result, TurnResult)
        self.assertEqual(result.model, EXPECTED_MODEL)
        self.assertIsNone(result.usage)  # 未采集：不得写成 0/空 dict
        self.assertEqual(result.exit_code, 0)
        # 本轮确实发起了命令且带超时（未真跑：被上面的 fake 拦截）
        self.assertEqual(calls[0][0][1:3], ["chat", "-c"])
        self.assertEqual(calls[0][2], 90)

    def test_reply_body_semantics_unchanged(self):
        """本次只补采集，不动已冻结的回复正文口径（正文仍含开头 CLI 提示行）。"""
        sid, reply = _parse_oneshot(REAL_STDOUT)
        self.assertIsNone(sid)  # 实测 `session_id:` 在 stderr（实物：REAL_STDERR）
        self.assertIn(f"session_id: {REAL_SESSION_ID}", REAL_STDERR)
        self.assertIn("Normalized model", reply)
        self.assertIn("牛肉面吃得怎么样？", reply)

    def test_shared_field_names_with_openclaw(self):
        """字段名与其它适配器一致：TurnResult 只有 model/usage 这一套键。"""
        self.assertTrue({"model", "usage", "session_id"} <= set(TurnResult.__dataclass_fields__))

    def test_real_artifact_corpus(self):
        """全量真实 Hermes 产物回归：每条 stdout 都能采到同一个生效模型 id。"""
        files = sorted((ROOT / "runs").glob("*/raw/hermes-*/s*_t*_stdout.txt"))
        if not files:
            self.skipTest("仓内历史产物目录 runs/ 不在本分发里，跳过全量回归")
        models = {_extract_model(path.read_text(encoding="utf-8", errors="replace")) for path in files}
        self.assertEqual(models, {EXPECTED_MODEL}, f"真实产物采集结果漂移：{sorted(models)}")
        self.assertGreaterEqual(len(files), 100, "全量回归夹具数量异常（runs/ 被截断？）")
        # usage 侧的实物依据：stderr 只有 session_id / 会话提示，没有任何 token 字段
        checked = 0
        for stdout_path in files[:8]:
            stderr_path = stdout_path.with_name(stdout_path.name.replace("_stdout", "_stderr"))
            if not stderr_path.is_file():
                continue
            text = stderr_path.read_text(encoding="utf-8", errors="replace")
            self.assertRegex(text, r"session_id: \S+")  # 定位 session_id 实在 stderr
            for token in ("usage", "token", "cost"):
                self.assertNotIn(token, text.lower(), f"stderr 实物里出现了 {token}，采集结论需重审")
            checked += 1
        self.assertGreater(checked, 0, "找不到配对的 stderr 实物")


if __name__ == "__main__":
    unittest.main()
