"""okb 引擎 · Agent 适配器基类（I-2 / I-3 / I-4 / I-7）

CLI 只认 `agent.yaml + capabilities()`（A 侧实施承诺）：接入新 Agent 只需新增一个适配器
子类，采集层与评分层零改动。本模块同时提供记忆文件（Markdown）解析的共享实现。
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)\s*[-—:：]\s*(.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_OBSERVED_RE = re.compile(r"<!--\s*observed:\s*(\d{4}-\d{2}-\d{2})")


def safe_config(config: dict) -> dict:
    """配置摘要落 manifest 前掩码敏感键（I-18）。"""
    out: dict = {}
    for key, value in config.items():
        if any(token in str(key).lower() for token in ("key", "token", "secret", "password")):
            out[key] = "<REDACTED>"
        elif isinstance(value, dict):
            out[key] = safe_config(value)
        else:
            out[key] = value
    return out


def mtime_iso(path: Path) -> str:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


@dataclass
class Capabilities:
    dialogue: bool = True
    memory: bool = False
    file: bool = False
    tool: bool = False
    notes: str = ""
    supports_time_anchor: bool = False

    def as_dict(self) -> dict:
        return {
            "dialogue": self.dialogue,
            "memory": self.memory,
            "file": self.file,
            "tool": self.tool,
            "notes": self.notes,
            "supports_time_anchor": self.supports_time_anchor,
        }


@dataclass
class TurnResult:
    reply_text: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    session_id: str | None = None
    model: str | None = None
    usage: dict | None = None
    memory_events: list[dict] = field(default_factory=list)  # 本轮真实应用的记忆写事件（mock 等）


@dataclass
class MemoryEntry:
    key: str
    value: str
    written_at: str | None
    written_at_source: str | None
    source: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "written_at": self.written_at,
            "written_at_source": self.written_at_source,
            "source": self.source,
        }


@dataclass
class MemorySnapshot:
    phase: str
    taken_at: str
    entries: list[MemoryEntry]
    coverage: dict
    observation: dict | None = None  # Optional vNext scope metadata; never a completeness proof.
    full_capture: dict | None = None  # Internal unredacted text; only runner may serialize after PII processing.

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "taken_at": self.taken_at,
            "entries": [e.as_dict() for e in self.entries],
            "coverage": self.coverage,
        }


@dataclass
class TaskContext:
    run_id: str
    task_id: str
    workspace: Path
    sessions: list[str] = field(default_factory=list)
    time_anchor: str | None = None
    sample: dict | None = None  # 本 task 的样本（mock 适配器需要 pii 声明做物化）


class AdapterError(RuntimeError):
    """Agent 调用失败（agent_error）。"""


class AdapterTimeout(AdapterError):
    """Agent 超时（timeout）。"""


class AgentAdapter(ABC):
    name: str = "abstract"
    agent_kind: str = "real"
    model_name: str | None = None
    temperature: float | None = None

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # ---- 能力声明（I-2） ----

    @abstractmethod
    def capabilities(self) -> Capabilities:
        ...

    # ---- 生命周期 ----

    @abstractmethod
    def reset(self, ctx: TaskContext) -> list[dict]:
        """恢复干净基线（I-10）。返回动作记录（落证据）。"""

    @abstractmethod
    def send(
        self,
        ctx: TaskContext,
        session: str,
        turn_index: int,
        text: str,
        session_first_turn: bool,
        timeout: float | None = None,
    ) -> TurnResult:
        """一轮 = 一次独立进程调用（CONTRACT §3）。"""

    @abstractmethod
    def probe_memory(self, phase: str, *, for_task_id: str | None = None) -> MemorySnapshot:
        """`for_task_id`：调用方声明这次探针属于哪个任务。

        记忆面在固定 home 目录的适配器（openclaw/hermes）可以忽略它；按任务目录存放记忆的
        模板适配器必须用它拒绝跨任务串证据。默认 None 保持既有调用形态兼容。
        """
        """任务前后各一次记忆快照（I-3）。"""

    def close(self) -> None:
        """释放资源（默认无操作）。"""

    # ---- 健康检查（CONTRACT §8：embed-server / CLI 存活） ----

    def cli_path(self) -> str | None:
        """适配器的可执行文件路径（用于健康检查）。"""
        return None

    def config_files(self) -> list[Path]:
        """I-5：参与 manifest 指纹的 Agent 配置文件（不含凭据文件）。"""
        return []

    def config_fingerprint(self) -> list[dict]:
        """配置文件清单 + sha256（内容不入库，只落哈希）。"""
        import hashlib

        out: list[dict] = []
        for path in self.config_files():
            item = {"path": str(path), "exists": path.is_file()}
            if item["exists"]:
                item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            out.append(item)
        return out

    def health(self) -> dict:
        """返回 {"ok": bool, "checks": [...]}；任务前后各查一次，死亡记 env_error。"""
        checks: list[dict] = []
        cli = self.cli_path()
        if cli:
            resolved = os.path.expanduser(cli)
            if os.path.isabs(resolved):
                exists = os.path.exists(resolved)
            else:
                exists = shutil.which(resolved) is not None
            checks.append({"name": "cli", "ok": bool(exists), "detail": cli})
        for cmd in self.config.get("health_commands", []):
            label = "cmd:" + " ".join(cmd)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                checks.append(
                    {
                        "name": label,
                        "ok": proc.returncode == 0,
                        "detail": (proc.stdout or proc.stderr).strip()[:200],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                checks.append({"name": label, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    def watch_health(self) -> list[dict]:
        """轻量周期性健康检查（长跑看门狗，轮间节流调用）；默认无检查。

        与 `health()` 的区别：必须是秒级、无副作用的本地探测（systemd/HTTP），
        因为它在任务执行中途会被反复调用。
        """
        return []

    # ---- 文件工作区（CONTRACT §6 的落地修正） ----

    def file_root(self, ctx: TaskContext) -> Path:
        """文件类证据（fixture / 文件读写）的根目录；默认 = task workspace。

        OpenClaw 的文件工具固定写 agent workspace（`~/.openclaw/workspace`），
        必须由适配器覆写，否则 fixture 与文件断言全部错位。
        """
        return ctx.workspace

    def workspace_ignore(self) -> set[str]:
        """file_root 顶层需要忽略的名字（系统目录 / 记忆面，避免污染文件证据）。"""
        return set()

    def workspace_keep(self) -> set[str] | None:
        """task 清理时保留的顶层条目；None = 整目录清空（task 独立工作区）。"""
        return None

    def memory_surface_paths(self) -> set[str]:
        """file_root 内属于记忆面的顶层路径——fixture 禁止命中（CONTRACT §6，ISS-02）。"""
        return set()

    def event_coverage(self) -> dict:
        """事件类证据的采集覆盖率声明（CONTRACT §5 资格表 / §15 #7）。

        真实 Agent 的 CLI 不暴露读取/写入事件 → 0；mock 适配器按实际采集覆盖覆写。
        """
        return {"memory_write_via_memory_api": 0, "memory_read_via_agent": 0}

    def validate_suite(self, suite_version: str | None, sample_ids: list[str]) -> None:
        """run 前校验（策略与 paper 对位 / 覆盖率前提）；默认无操作。"""
        return None

    def manifest_extra(self) -> dict:
        """写入 manifest 顶层的额外元信息（如 mock 的策略指纹）；默认无。"""
        return {}

    # ---- 时间锚定归一化（I-15 方案 2） ----

    def normalize_time_memory(self, anchor: str, session_start_real: datetime.datetime) -> dict:
        """会话结束后把 Agent 侧记忆的时间戳归一化到锚点；默认无操作。"""
        return {"supported": False}

    # ---- 共享工具 ----

    @staticmethod
    def session_key(run_id: str, task_id: str, session: str) -> str:
        """Agent 侧会话命名空间：<run_id>--<task_id>--<session>（CONTRACT §2）。"""
        return f"{run_id}--{task_id}--{session}"


def parse_sectioned_memory(
    text: str, source: str, file_mtime: str, section_sep: str = "§"
) -> list[MemoryEntry]:
    """解析 Hermes 风格的分段记忆（ISS-13）：纯文本段，段间以**单独一行** `§` 分隔。

    与 parse_markdown_memory 同款约定：key = 段首 80 字符；本格式无条目级时间戳，
    written_at 一律文件 mtime（written_at_source=file_mtime）。
    """
    pattern = re.compile(rf"^[ \t]*{re.escape(section_sep)}[ \t]*$", re.M)
    entries: list[MemoryEntry] = []
    for segment in pattern.split(text):
        value = segment.strip()
        if not value:
            continue
        entries.append(
            MemoryEntry(
                key=value[:80],
                value=value,
                written_at=file_mtime,
                written_at_source="file_mtime",
                source=source,
            )
        )
    return entries


def parse_markdown_memory(text: str, source: str, file_mtime: str) -> list[MemoryEntry]:
    """把 MEMORY.md / 每日记忆解析为条目列表。

    key 合成：条目首行截断 80 字符（CONTRACT §15 #1（原 §12.1））；
    written_at：优先行首时间戳（openclaw 每日文件），否则文件 mtime 并注明来源。
    """
    entries: list[MemoryEntry] = []
    heading = ""
    pending_observed: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("<!--"):
            match = _OBSERVED_RE.search(line)
            if match:
                pending_observed = match.group(1)
            continue
        if line.lstrip().startswith("#"):
            heading = line.lstrip("#").strip()
            continue
        m = _BULLET_RE.match(line)
        if m:
            content = m.group(1).strip()
        elif _TS_RE.match(line):
            content = line.strip()
        elif heading:
            content = line.strip()
        else:
            continue
        if not content:
            continue
        written_at: str | None
        written_at_source: str | None
        value = content
        ts = _TS_RE.match(content)
        if ts:
            written_at = ts.group(1).replace(" ", "T")
            written_at_source = "entry_timestamp"
            value = ts.group(2).strip() or content
        elif pending_observed:
            written_at = pending_observed + "T00:00:00"
            written_at_source = "observed_metadata"
            pending_observed = None
        else:
            written_at = file_mtime
            written_at_source = "file_mtime"
        entries.append(
            MemoryEntry(
                key=value[:80],
                value=value,
                written_at=written_at,
                written_at_source=written_at_source,
                source=source,
            )
        )
    return entries
