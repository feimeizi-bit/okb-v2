#!/usr/bin/env python3
"""S0 记忆探针正向对照（ISS-13，B 要求②）——写一条可指认记忆 → 快照 entries>0 才算探针成立。

用法（VM 内、仓库根）：
    python3 deploy/probe-memory-readback.py [--agent hermes] [--agent openclaw]

链条：reset（基线干净）→ 前置快照 → 一条含 canary 的写入请求（真实 LLM，1 次调用）
→ 后置快照 → 判据：post entries ≥ 1（B 口径）；canary 命中仅作「可指认」信息上报。

输出：每 agent 一行 JSON（reply/coverage/verdict），退出码 0=全 PASS / 1=有 FAIL。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.adapter import TaskContext  # noqa: E402
from engine.runner import make_adapter  # noqa: E402

CANARY = "探针蓝色雨伞"
# 实测（2026-09-24）：同 prompt 下 Hermes 会偶发「只回复已记住、不真正落盘」——
# 故对照加严：显式要求调用记忆工具 + 未落盘时重试一次（B 口径 entries>0 才算探针成立）。
MSG = (
    "这是一条必须长期保存的用户信息。请调用你的记忆/持久化工具，把它写入长期记忆"
    "（不是只在本轮对话里记住）：我的出门暗号是「{canary}」。"
    "写入成功后只回复：已记住。"
)


def probe_one(agent: str) -> dict:
    run_dir = ROOT / "runs" / f"_probe-memory-{agent}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    ws = run_dir / "work"
    ws.mkdir(parents=True)

    adapter = make_adapter(agent, None)
    ctx = TaskContext(run_id="probe-memory", task_id=f"{agent}-probe", workspace=ws, sessions=["s1"])
    reset_actions = adapter.reset(ctx)
    pre = adapter.probe_memory("before")

    attempts: list[dict] = []
    post = pre
    for attempt in range(1, 3):  # 最多两次：模型偶发宣称已记住但不落盘
        turn = adapter.send(ctx, "s1", attempt - 1, MSG.format(canary=CANARY), attempt == 1)
        post = adapter.probe_memory("after")
        values = [e.value for e in post.entries]
        attempts.append(
            {
                "attempt": attempt,
                "post_entries": len(post.entries),
                "canary_hit": any(CANARY in v for v in values),
                "reply": (turn.reply_text or "")[:80],
                "stdout_tail": (turn.stdout or "")[-200:],
            }
        )
        if post.entries:
            break

    adapter.close()
    ok = len(post.entries) > 0  # B 口径：entries>0 才算 memory=true 成立
    return {
        "agent": agent,
        "reset_ok": all(a.get("ok") for a in reset_actions) if reset_actions else True,
        "pre_entries": len(pre.entries),
        "post_entries": len(post.entries),
        "canary_hit": attempts[-1]["canary_hit"] if attempts else False,
        "attempts": attempts,
        "coverage": post.coverage,
        "verdict": "PASS" if ok else "FAIL",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆探针正向对照（S0）")
    ap.add_argument("--agent", action="append", default=None, help="可重复；缺省 hermes")
    args = ap.parse_args()
    results = [probe_one(a) for a in (args.agent or ["hermes"])]
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
