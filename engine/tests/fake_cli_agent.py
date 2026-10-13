#!/usr/bin/env python3
"""假智能体 CLI（测试资产，不是交付物）：给 cli_template 适配器当可取证的对端。

用法：
    fake_cli_agent.py --state <file> chat --session S --json     # 读 stdin，回 JSON 信封
    fake_cli_agent.py --state <file> memory export --json
    fake_cli_agent.py --state <file> memory clear --yes
环境变量控制故障注入：FAKE_CLI_EXIT=退出码 / FAKE_CLI_SLEEP=秒 / FAKE_CLI_NOJSON=1 /
FAKE_CLI_NOMODEL=1（模拟"CLI 根本不报 model"，用来验证适配器不编造观测值）。
每次调用都把 argv 与 stdin 追加写进 <state>.calls，供测试断言"对话文本走 stdin、不进 argv"。
"""
import json
import os
import sys
from pathlib import Path
import time

argv = sys.argv[1:]
state = Path(argv[argv.index("--state") + 1])
rest = [a for i, a in enumerate(argv) if i not in (argv.index("--state"), argv.index("--state") + 1)]
data = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {"messages": [], "memory": [
    {"key": "seed", "value": "预置记忆条目"}]}
calls = state.with_suffix(".calls")
stdin_text = ""
if rest and rest[0] == "chat":
    stdin_text = sys.stdin.read()
with calls.open("a", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps({"argv": rest, "stdin": stdin_text}, ensure_ascii=False) + "\n")

sleep = os.environ.get("FAKE_CLI_SLEEP")
if sleep:
    time.sleep(float(sleep))
code = int(os.environ.get("FAKE_CLI_EXIT", "0"))
if code:
    sys.stderr.write("fake cli failed on purpose\n")
    raise SystemExit(code)

if rest[:1] == ["memory"] and rest[1:3] == ["clear", "--yes"]:
    data = {"messages": [], "memory": [{"key": "seed", "value": "预置记忆条目"}]}
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "cleared": True}, ensure_ascii=False))
    raise SystemExit(0)

if rest[:1] == ["memory"] and rest[1:3] == ["export", "--json"]:
    print(json.dumps({"entries": data["memory"], "count": len(data["memory"])}, ensure_ascii=False))
    raise SystemExit(0)

if rest[:1] == ["chat"]:
    session = rest[rest.index("--session") + 1] if "--session" in rest else "default"
    text = stdin_text
    if os.environ.get("FAKE_CLI_NOJSON"):
        print("not json at all: " + text[:40])
        raise SystemExit(0)
    reply = "echo:" + text
    data["messages"].append({"session": session, "text": text, "reply": reply})
    # 真写产物：只有声明 file 能力且确实落盘，才允许 capabilities.file=true（否则调度器会
    # 如实 skip 带 file_content 锚点的样本 —— 那是正确的能力闸，不是故障）。
    import re as _re
    for name in _re.findall(r"写(?:进|到|入|成)\s*([^\s，。、：:（）()]+\.(?:md|yaml|yml|json|txt))", text):
        target = Path.cwd() / name
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# fake agent artifact (test double)", ""]
        lines += ["- %s: %s" % (m.get("key"), m.get("value")) for m in data["memory"]]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        reply += " [written:" + name + "]"
    if text.startswith("记住 "):
        data["memory"].append({"key": "m%d" % len(data["memory"]), "value": text[3:]})
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
    envelope = {"responses": [{"output": [{"text": reply}]}],
                "session_id": session,
                "usage": {"prompt_tokens": len(text) // 4 + 1, "completion_tokens": len(reply) // 4 + 1}}
    if not os.environ.get("FAKE_CLI_NOMODEL"):
        envelope["model"] = "fake-model-1"
    print(json.dumps(envelope, ensure_ascii=False))
    raise SystemExit(0)

sys.stderr.write("fake cli: unsupported command %r\n" % (rest,))
raise SystemExit(64)
