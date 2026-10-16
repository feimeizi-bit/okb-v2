"""okb 引擎 · Hermes Agent 适配器

实测校准（`docs/contract-a-confirm-v0.1.md` §2/§3）：

- 单轮：`hermes chat -c <title> --create-if-missing -Q --in <workspace> -q <msg>`
  （**首轮**带 `--create-if-missing`；后续轮去掉；顶层 `-z` 不支持创建命名会话）
- 回复抽取：`-Q` 输出首行 `session_id: ...`，其余为回复正文
  （**实测校正**：`session_id:` 行在 **stderr**，不在 stdout——`runs/run_v02_d1/raw/hermes-*`、
  `runs/run_v02_first/raw/hermes-*` 共 120 轮里 stderr 120/120 命中、stdout 0/120 命中；
  `TurnResult.session_id` 当前无下游消费者（runner 落证据行用的是逻辑会话名 `s1`），故本次
  **不改**抽取行为，只把事实写清楚，避免后来者按旧注释理解现状）
- model/usage（补 `docs/s1-observation-source-map-batch14.md` 与交接文档 §5 自认的缺口）：
  * **model 可采**：`-Q` 的 stdout 开头固定是 Hermes CLI 自己打的归一化提示，仓内真实产物
    120/120 命中（例 `runs/run_v02_d1/raw/hermes-retain_allergy_001/s1_t0_stdout.txt:1-2`，
    CRLF，并按终端宽度在 `for ` 处折行）：
    `⚠️  Normalized model 'deepseek/deepseek-v4-pro' to 'deepseek-v4-pro' for` / `deepseek.`
    取 `to '...'` 里的**归一化后生效模型 id**（=`deepseek-v4-pro`），与 OpenClaw 侧
    `meta.agentMeta.model` 同形（对照 `runs/run_v02_d1/evidence.jsonl` 的 `payload.model` 与
    `system_task_summary.jsonl` 的 `"models": ["deepseek-v4-pro"]`），跨 Agent 无需二次归一即可对齐。
    只认「stdout 首行即该提示行」这一形态：回复正文里出现的同名文本一律不采信（既防伪造，也不违反
    「不能从回复里的 `model:`、配置默认值或猜测日志格式解析身份」——本行是 CLI 自身的运行期诊断，
    不是配置值、不是回复内容，且格式来自仓内实物而非猜测）。
  * **usage 未采集**：`-Q` 的 stdout/stderr/request 在全部真实产物里没有任何 token/费用字段
    （grep `usage|token|cost` 零命中；hermes 侧 `system_task_summary.jsonl` 为
    `"models": [], "usage": null`，同批 openclaw 侧有值）。因此 `TurnResult.usage` 显式保持 `None`
    （不是漏写）：证据里就是 `payload.usage = null`，禁止用 0/空串冒充观测值（反面例子：
    `deploy/embed-server/server.py` 的 usage 两字段写死 0，属另案，本适配器不照抄）。
    最小可行补法见 `_extract_model` 下方注释块「usage 缺口的取证步骤」。
- 记忆面：`~/.hermes/memories/{MEMORY.md,USER.md}`（条目级无时间戳，written_at 用 mtime）
- reset：`memory reset --yes` + 恢复基线文件；会话靠唯一标题天然隔离
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from engine.adapter import (
    AdapterError,
    AdapterTimeout,
    AgentAdapter,
    Capabilities,
    MemorySnapshot,
    TaskContext,
    TurnResult,
    parse_sectioned_memory,
)
from engine.collectors import now_iso
from engine.redact import redact_argv
from engine.timeanchor import parse_anchor

_BASELINE_FILES = ("MEMORY.md", "USER.md")


def _parse_memory_text(text: str, source: str, file_mtime: str):
    """Hermes 记忆文件形态（ISS-13 实证）：纯文本条目，多条目间以**单独一行 `§`** 分隔；
    **单条目文件无分隔符**。据此一律走分段解析器——旧实现「无 § 回退 markdown」会让
    单行/纯文本内容恒记 0 条（正向对照实测：canary 已在文件里，条目数仍为 0）。"""
    return parse_sectioned_memory(text, source, file_mtime)


class HermesAdapter(AgentAdapter):
    name = "hermes"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        self.cli = os.path.expanduser(str(cfg.get("cli", "~/.local/bin/hermes")))
        self.home = Path(os.path.expanduser(str(cfg.get("home", "~/.hermes"))))
        self.memories = self.home / "memories"
        self.baseline = Path(os.path.expanduser(str(cfg.get("baseline_dir", "~/.okb/baseline/hermes"))))
        self.supports_time_anchor = bool(cfg.get("supports_time_anchor", True))
        self.turn_timeout = float(cfg.get("turn_timeout_seconds", 180))

    def cli_path(self) -> str | None:
        return self.cli

    def config_files(self) -> list[Path]:
        return [self.home / "config.yaml"]

    # ---- 能力 ----

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dialogue=True,
            memory=True,
            file=True,
            tool=True,
            notes="内置记忆 = memories/{MEMORY.md,USER.md}；无条目级时间戳（written_at 用 mtime）",
            supports_time_anchor=self.supports_time_anchor,
        )

    # ---- 生命周期 ----

    def reset(self, ctx: TaskContext) -> list[dict]:
        actions: list[dict] = [self._run(["memory", "reset", "--yes"])]
        actions.append(self._ensure_baseline())
        return actions

    def _ensure_baseline(self) -> dict:
        if self.baseline.exists():
            return self._restore_baseline()
        self.baseline.mkdir(parents=True, exist_ok=True)
        for name in _BASELINE_FILES:
            src = self.memories / name
            if src.is_file():
                shutil.copy2(src, self.baseline / name)
        return {"cmd": f"ensure_baseline {self.baseline}", "returncode": 0, "ok": True, "duration_ms": 0}

    def _restore_baseline(self) -> dict:
        self.memories.mkdir(parents=True, exist_ok=True)
        for name in _BASELINE_FILES:
            target = self.memories / name
            if target.exists():
                target.unlink()
        for name in _BASELINE_FILES:
            src = self.baseline / name
            if src.is_file():
                shutil.copy2(src, self.memories / name)
        # 空记忆面契约（2026-10-11 实战修复）：hermes memory reset 会删除内容文件，
        # 恢复后两张记忆文件可能不存在 ⇒ before 快照 files_scanned=0，被保守证据门
        # 整单拒判（实测 276/276 hermes 任务被挡）。补建空文件：空记忆=空文件，
        # 采集侧由此可以证明「扫过、确为空」，而不是「没扫到」。
        ensured: list[str] = []
        for name in _BASELINE_FILES:
            target = self.memories / name
            if not target.exists():
                target.write_text("", encoding="utf-8", newline="\n")
                ensured.append(name)
        result = {"cmd": f"restore_baseline {self.baseline}", "returncode": 0, "ok": True, "duration_ms": 0}
        if ensured:
            result["note"] = "空记忆面契约：补建空文件 " + ", ".join(ensured)
        return result

    # ---- 单轮调用 ----

    def send(
        self,
        ctx: TaskContext,
        session: str,
        turn_index: int,
        text: str,
        session_first_turn: bool,
        timeout: float | None = None,
    ) -> TurnResult:
        title = self.session_key(ctx.run_id, ctx.task_id, session)
        workspace = Path(ctx.workspace).resolve()
        argv = [self.cli, "chat", "-c", title]
        if session_first_turn:
            argv.append("--create-if-missing")
        argv += ["-Q", "--in", str(workspace), "-q", text]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.turn_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterTimeout(f"hermes 单轮超时（{timeout or self.turn_timeout}s）") from exc
        duration_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            raise AdapterError(
                f"hermes 退出码 {proc.returncode}：{(proc.stderr or proc.stdout or '')[:400]}"
            )
        sid, reply = _parse_oneshot(proc.stdout or "")
        return TurnResult(
            reply_text=reply,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            session_id=sid,
            model=_extract_model(proc.stdout or ""),
            # usage 显式不采（不是漏写）：Hermes `-Q` 无任何 usage 输出来源，见模块 docstring
            # 与下方「usage 缺口的取证步骤」。采不到就 None，不写 0/空 dict 冒充观测值。
            usage=None,
        )

    # ---- 时间锚定归一化（I-15 方案 2） ----

    def normalize_time_memory(self, anchor: str, session_start_real) -> dict:
        """内置记忆无条目级时间戳：把 memories/*.md 的 mtime 设为锚点。"""
        ts = parse_anchor(anchor).timestamp()
        files: list[str] = []
        for name in _BASELINE_FILES:
            path = self.memories / name
            if path.is_file():
                os.utime(path, (ts, ts))
                files.append(name)
        return {"mtime_set": files}

    # ---- 记忆快照 ----

    def probe_memory(self, phase: str, *, for_task_id: str | None = None) -> MemorySnapshot:
        started_at = now_iso()
        entries = []
        files = 0
        total_bytes = 0
        sources: list[str] = []
        from engine.memory_capture import capture_files
        capture = capture_files(self.memories, list(_BASELINE_FILES))
        for doc in capture['documents']:
            name = doc['path']
            text = doc['content'].replace('\r\n', '\n').replace('\r', '\n')
            files += 1
            total_bytes += len(text.encode("utf-8"))
            stamp = datetime.datetime.fromtimestamp(doc['source_mtime_ns'] / 1e9).astimezone().isoformat(timespec='seconds')
            entries.extend(_parse_memory_text(text, name, stamp))
            sources.append(name)
        coverage = {
            "files_scanned": files,
            "entries_scanned": len(entries),
            "bytes_scanned": total_bytes,
            "sources": sources,
        }
        completed_at = now_iso()
        from engine.observation import file_scope
        scope = file_scope(self.memories, list(_BASELINE_FILES), 'sectioned-v1', started_at, completed_at, full_capture=True)
        return MemorySnapshot(phase=phase, taken_at=completed_at, entries=entries,
                              coverage=coverage, observation=scope, full_capture=capture)

    # ---- CLI 辅助 ----

    def _run(self, args: list[str], timeout: float = 120) -> dict:
        argv = [self.cli, *args]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.home),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            rc = proc.returncode
            out = proc.stdout or ""
            err = proc.stderr or ""
        except subprocess.TimeoutExpired:
            rc = -1
            out = ""
            err = f"timeout {timeout}s"
        return {
            "cmd": " ".join(redact_argv(argv)),
            "returncode": rc,
            "ok": rc == 0,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "stdout_tail": out[-400:],
            "stderr_tail": err[-400:],
        }


# 归一化提示里的模型身份：`Normalized model '<配置值>' to '<生效值>' for <provider>`
# （仓内实物：`Normalized model 'deepseek/deepseek-v4-pro' to 'deepseek-v4-pro' for\r\ndeepseek.`）
_MODEL_NOTICE_RE = re.compile(
    r"Normalized model\s+'(?P<configured>[^']*)'\s+to\s+'(?P<effective>[^']*)'\s+for\s+"
    r"(?P<provider>[^\s'\"]+)",
    re.IGNORECASE,
)
# 提示行会被终端宽度拆行（实测 2 行）；只允许向前拼到第 5 行，避免深入回复正文。
_NOTICE_MAX_WRAP_LINES = 5


def _is_cli_notice_line(line: str) -> bool:
    """CLI 诊断行判据：首字符不是字母/数字/下划线（实物形态是 `⚠️`）。

    Python 的 `str.isalnum()` 对中日韩文字也返回 True，所以回复正文（无论中英）不会被
    误归成诊断行；只有 emoji/符号开头的行才归为 CLI 诊断。
    """
    return bool(line) and not (line[0].isalnum() or line[0] == "_")


def _extract_model(stdout: str) -> str | None:
    """从 `-Q` stdout **开头的** CLI 诊断行采实际生效模型 id；采不到就返回 `None`。

    只取 `to '...'` 的归一化后值：与 OpenClaw `meta.agentMeta.model` 同形，证据层无需二次归一；
    配置侧带 provider 前缀的那个值不入（配置声明 ≠ 观测，见
    `docs/s1-observation-source-map-batch14.md`：“配置model不是返回model”）。
    严格限定在 stdout 开头且首行必须是诊断行，否则视为回复正文→不采（防伪造，也不靠配置默认值猜）。
    新版 Hermes 若不再打这行，本采集自然回退 `None`（未采集），不做任何形式的猜测。
    """
    lines = [
        line.strip()
        for line in (stdout or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    head = next((line for line in lines if line), "")
    if not _is_cli_notice_line(head):
        return None
    for cut in range(1, min(_NOTICE_MAX_WRAP_LINES, len(lines)) + 1):
        match = _MODEL_NOTICE_RE.search(" ".join(lines[:cut]))
        if match:
            return (match.group("effective") or "").strip() or None
    return None


# ---- usage 缺口的取证步骤（本适配器故意不实现，先钉住来源）----
# Hermes `-Q` 的 stdout/stderr 里没有 token/费用字段（仓内 120 轮实物 grep 零命中），所以
# `TurnResult.usage` 永远是 None，`system_task_summary.jsonl` / `usage.json` 的 hermes 侧仍是
# 「半边账」（见 `notes/runlog.md`、`notes/t0-precheck-checklist.md` 第 3 条）。
# 要补上必须先在 openKylin VM 上拿到**实物**，而不是猜 flag：
#   1) 零 token 先看能力面：`~/.local/bin/hermes --help` 与 `~/.local/bin/hermes chat --help`，
#      查是否有结构化输出/用量开关（如 `--json` / `--usage` / `--verbose`）；
#   2) 零 token 只看会话落盘：`ls -R ~/.hermes` 并找一个已知 session_id（stderr 里的
#      `session_id: <id>`）对应的会话/日志文件，grep `token|usage|cost` 看是否已有逐 turn 记录；
#   3) 两条都无实物依据时，只能让 provider 侧（DeepSeek 账户用量接口）做总量对账，不能当逐 turn
#      观测值入证据；确认到具体字段后，按 OpenClaw 同键名（`input/output/cacheRead/cacheWrite/
#      reasoningTokens/total`）填 `TurnResult.usage`，runner 侧累计与预算闸无需改动。


def _parse_oneshot(stdout: str) -> tuple[str | None, str]:
    """解析 `-Q` 输出：首行 session_id，其余为回复正文。

    实物注意：`session_id:` 行实测在 **stderr**（仓内 120/120），本函数只走 stdout 所以恒返回
    `sid=None`；回复正文仍包含开头的 CLI 归一化提示（没例行为没变，model 另走 `_extract_model`）。
    改这两处行为会动已冻结的回复文本/评分入参，需主会话与用户先定（见交付报告）。
    """
    sid: str | None = None
    reply_lines: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if sid is None and stripped.startswith("session_id:"):
            sid = stripped.split(":", 1)[1].strip() or None
            continue
        reply_lines.append(line)
    return sid, "\n".join(reply_lines).strip()
