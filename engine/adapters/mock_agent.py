#!/usr/bin/env python3
"""okb · mock 考生进程（CONTRACT §14：独立进程 + stdin/stdout JSONL）。

由 `engine/adapters/mock.py` 拉起，一行请求一行响应：

    引擎 → mock：{"type":"turn","run_id":"...","task_id":"mock-<sample>","session":"s1","turn":1,"message":"..."}
    mock → 引擎：{"reply_text":"...","memory_ops":[...],"file_ops":[...]}   # 缺省字段等价于空数组
    引擎 → mock：{"type":"bye"} → 正常退出

本进程是**纯策略解析器**：只按 B 的 `tools/mock_policy.py` 语义选记录并返回命令；
ops 的物化与应用由适配器负责（§14：ops 是状态变更命令，不得直接写证据）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import mock_policy  # noqa: E402


def sample_id_of(task_id: str) -> str:
    """`mock-<sample>[-rN]` → `<sample>`。"""
    sid = re.sub(r"-r\d+$", "", str(task_id))
    return sid[len("mock-"):] if sid.startswith("mock-") else sid


def main() -> int:
    parser = argparse.ArgumentParser(description="okb mock 考生进程（CONTRACT §14）")
    parser.add_argument("--policy", required=True)
    args = parser.parse_args()

    try:
        policy = mock_policy.Policy.load(args.policy)
    except Exception as exc:  # noqa: BLE001 —— 策略侧错误：启动失败，由适配器映射 env_error
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr, flush=True)
        return 2
    record_index = {
        id(rec): (sid, idx)
        for sid, recs in policy.samples.items()
        for idx, rec in enumerate(recs)
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"非法 JSON：{exc}"}), flush=True)
            continue
        if msg.get("type") == "bye":
            break
        if msg.get("type") != "turn":
            print(json.dumps({"error": f"未知消息类型：{msg.get('type')!r}"}), flush=True)
            continue
        sid = sample_id_of(str(msg.get("task_id", "")))
        try:
            record, matched = mock_policy.resolve(policy, sid, str(msg.get("session", "")), int(msg.get("turn", 1)))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
            continue
        fired = None
        if matched is not None:
            fired = record_index.get(id(matched))
        print(
            json.dumps(
                {
                    "reply_text": record.reply_text,
                    "memory_ops": record.memory_ops,
                    "file_ops": record.file_ops,
                    "fired": {"sample_id": fired[0], "index": fired[1]} if fired else None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
