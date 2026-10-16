"""okb 引擎 · 时间锚定的事后归一化（I-15 方案 2，A 侧确认）

不修改被测进程时钟（避免与 TLS 证书有效期冲突），而是：

1. **对话层**：会话首轮注入「（当前时间：<anchor>）」——Agent 在会话内可感知剧本时间
2. **会话结束后归一化**（本模块）：
   - workspace 文件 mtime → 会话锚点
   - OpenClaw 每日记忆 `memory/YYYY-MM-DD.md`：文件名改到锚点日期 + 条目时间戳前缀按
     `delta = 锚点 - 会话真实开始时间` 平移，再重建索引
   - Hermes 内置记忆：文件 mtime → 锚点（无条目级时间戳）

归一化动作由 runner 落成 `system` 证据（action=time_normalization），可审计。
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

_ENTRY_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?P<rest>\s*[-—:：]\s*.*)$"
)


def parse_anchor(value: str) -> datetime.datetime:
    """解析 ISO 锚点；无时区按本机时区处理。"""
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
    return dt


def shift_entry_timestamps(text: str, delta: datetime.timedelta) -> tuple[str, int]:
    """把行首 `YYYY-MM-DD HH:MM[:SS] - 内容` 的时间戳整体平移 delta。

    返回 (新文本, 平移条目数)。不匹配的行原样保留。
    """
    out: list[str] = []
    count = 0
    for line in text.splitlines(keepends=True):
        match = _ENTRY_TS_RE.match(line)
        if not match:
            out.append(line)
            continue
        original = datetime.datetime(
            int(match["date"][:4]),
            int(match["date"][5:7]),
            int(match["date"][8:10]),
            int(match["hour"]),
            int(match["minute"]),
            int(match["second"] or 0),
        )
        shifted = original + delta
        if match["second"] is not None:
            prefix = shifted.strftime("%Y-%m-%d %H:%M:%S")
        else:
            prefix = shifted.strftime("%Y-%m-%d %H:%M")
        newline = "\n" if line.endswith("\n") else ""
        out.append(prefix + match["rest"] + newline)
        count += 1
    return "".join(out), count


def normalize_openclaw_memory(
    memory_dir: Path,
    anchor: datetime.datetime,
    session_start_real: datetime.datetime,
) -> dict:
    """重写 OpenClaw 每日记忆：改文件名 + 平移条目时间戳（必要时合并同日期文件）。"""
    if not memory_dir.is_dir():
        return {"renamed": [], "entries_shifted": 0}
    delta = anchor - session_start_real
    target_name = anchor.strftime("%Y-%m-%d") + ".md"
    actions: list[dict] = []
    total_shifted = 0
    for path in sorted(memory_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        shifted, count = shift_entry_timestamps(text, delta)
        total_shifted += count
        target = memory_dir / target_name
        if path == target:
            if shifted != text:
                path.write_text(shifted, encoding="utf-8", newline="\n")
            actions.append({"file": path.name, "action": "rewrite", "entries_shifted": count})
        elif target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace").rstrip("\n")
            target.write_text(existing + "\n" + shifted, encoding="utf-8", newline="\n")
            path.unlink()
            actions.append({"file": path.name, "action": f"merge_into:{target_name}", "entries_shifted": count})
        else:
            path.unlink()
            target.write_text(shifted, encoding="utf-8", newline="\n")
            actions.append({"file": path.name, "action": f"rename:{target_name}", "entries_shifted": count})
    return {"renamed": actions, "entries_shifted": total_shifted}


def rewrite_observed_dates(text: str, real_date: str, anchor_date: str) -> tuple[str, int]:
    """把 `<!-- observed: <real_date>` 改成锚点日期（只改本会话真实日期的那些）。

    OpenClaw 的 USER.md / MEMORY.md 约定用 observed 元数据记录条目日期；
    不归一化的话，剧本日期会穿帮成真实日期。
    """
    pattern = re.compile(r"(<!--\s*observed:\s*)" + re.escape(real_date))
    new_text, count = pattern.subn(r"\g<1>" + anchor_date, text)
    return new_text, count


def set_mtimes(root: Path, rel_paths: list[str], anchor: datetime.datetime) -> int:
    """把 workspace 内指定相对路径的文件 mtime/atime 设为锚点时间；返回处理文件数。"""
    ts = anchor.timestamp()
    count = 0
    for rel in rel_paths:
        target = root / rel
        if target.is_file():
            os.utime(target, (ts, ts))
            count += 1
    return count
