"""okb 引擎 · OpenClaw 适配器

实测校准（`docs/contract-a-confirm-v0.1.md` §2/§3）：

- 单轮：`openclaw agent --local --agent okb --session-key agent:okb:<key> --json -m <msg>`
- 回复抽取：`--json` 的 `payloads[].text`；`meta.agentMeta` 带 sessionId / model / usage
- 记忆面：`workspace/MEMORY.md`、`USER.md`、`memory/YYYY-MM-DD.md`（每日记忆）
- reset：恢复基线文件 → `memory forget --session <keys>` → `memory reset --yes` → `memory index`
"""
from __future__ import annotations

import datetime
import json
import os
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
    parse_markdown_memory,
)
from engine.collectors import now_iso
from engine.redact import redact_argv
from engine.timeanchor import normalize_openclaw_memory, parse_anchor, rewrite_observed_dates

_BASELINE_FILES = ("MEMORY.md", "USER.md")
_BASELINE_DIRS = ("memory",)
_SYSTEM_KEEP = {"AGENTS.md", "SOUL.md", "IDENTITY.md", "BOOTSTRAP.md", ".git"}


class OpenClawAdapter(AgentAdapter):
    name = "openclaw"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        self.cli = str(cfg.get("cli", "openclaw"))
        self.agent_id = str(cfg.get("agent_id", "okb"))
        self.home = Path(os.path.expanduser(str(cfg.get("home", "~/.openclaw"))))
        self.workspace = Path(
            os.path.expanduser(str(cfg.get("workspace", str(self.home / "workspace"))))
        )
        self.baseline = Path(os.path.expanduser(str(cfg.get("baseline_dir", "~/.okb/baseline/openclaw"))))
        self.supports_time_anchor = bool(cfg.get("supports_time_anchor", True))
        self.turn_timeout = float(cfg.get("turn_timeout_seconds", 180))
        service = str(cfg.get("embed_server_service", "embed-server"))
        cfg.setdefault("health_commands", [["systemctl", "is-active", service]])

    def cli_path(self) -> str | None:
        return self.cli

    def watch_health(self) -> list[dict]:
        """长跑看门狗：轮间轻量检查 embed-server（任务中途服务死亡早发现）。"""
        service = str(self.config.get("embed_server_service", "embed-server"))
        name = f"watch:{service}"
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", service], capture_output=True, text=True, timeout=10
            )
            state = (proc.stdout or proc.stderr or "").strip()
            return [{"name": name, "ok": proc.returncode == 0 and state == "active", "detail": state}]
        except Exception as exc:  # noqa: BLE001
            return [{"name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}]

    def config_files(self) -> list[Path]:
        return [self.home / "openclaw.json"]

    def file_root(self, ctx: TaskContext) -> Path:
        """OpenClaw 文件工具固定写 agent workspace（实测），fixture 与文件断言以此为准。"""
        return self.workspace

    def workspace_ignore(self) -> set[str]:
        return {".git", *_SYSTEM_KEEP, *_BASELINE_FILES, *_BASELINE_DIRS}

    def workspace_keep(self) -> set[str]:
        # 记忆面文件也要保留：首次运行时基线捕获必须能看到它们（否则新实例基线为空）；
        # BOOTSTRAP.md 保留是为了不触发 OpenClaw 的"工作区被清空"保护（拒绝重新播种）。
        return {*_SYSTEM_KEEP, *_BASELINE_FILES}

    def memory_surface_paths(self) -> set[str]:
        return {*_BASELINE_FILES, *_BASELINE_DIRS}

    # ---- 能力 ----

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dialogue=True,
            memory=True,
            file=True,
            tool=True,
            notes="记忆面 = workspace/{MEMORY.md,USER.md,memory/*.md}；短期召回由 memory forget 清理",
            supports_time_anchor=self.supports_time_anchor,
        )

    # ---- 生命周期 ----

    def reset(self, ctx: TaskContext) -> list[dict]:
        actions: list[dict] = [self._ensure_baseline()]
        keys = [
            f"agent:{self.agent_id}:{self.session_key(ctx.run_id, ctx.task_id, s)}"
            for s in ctx.sessions
        ]
        if keys:
            forget_args = ["memory", "forget", "--agent", self.agent_id]
            for key in keys:
                forget_args += ["--session", key]
            actions.append(self._run_with_retry(forget_args))
        # 多 agent 并存时必须显式 --agent，否则命令报 "no explicit owner" 或误操作全部 agent
        actions.append(self._run_with_retry(["memory", "reset", "--yes", "--agent", self.agent_id]))
        actions.append(self._run_with_retry(["memory", "index", "--agent", self.agent_id]))
        return actions

    def _ensure_baseline(self) -> dict:
        """首次运行时抓取当前记忆文件作为基线（应在环境干净时调用）。"""
        if self.baseline.exists():
            return self._restore_baseline()
        self.baseline.mkdir(parents=True, exist_ok=True)
        for name in _BASELINE_FILES:
            src = self.workspace / name
            if src.is_file():
                shutil.copy2(src, self.baseline / name)
        for name in _BASELINE_DIRS:
            src = self.workspace / name
            if src.is_dir():
                shutil.copytree(src, self.baseline / name, dirs_exist_ok=True)
        return {"cmd": f"ensure_baseline {self.baseline}", "returncode": 0, "ok": True, "duration_ms": 0}

    def _restore_baseline(self) -> dict:
        for name in _BASELINE_FILES:
            target = self.workspace / name
            if target.exists():
                target.unlink()
        for name in _BASELINE_DIRS:
            target = self.workspace / name
            if target.exists():
                shutil.rmtree(target)
        for name in _BASELINE_FILES:
            src = self.baseline / name
            if src.is_file():
                shutil.copy2(src, self.workspace / name)
        for name in _BASELINE_DIRS:
            src = self.baseline / name
            if src.is_dir():
                shutil.copytree(src, self.workspace / name, dirs_exist_ok=True)
        return {"cmd": f"restore_baseline {self.baseline}", "returncode": 0, "ok": True, "duration_ms": 0}

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
        key = f"agent:{self.agent_id}:{self.session_key(ctx.run_id, ctx.task_id, session)}"
        argv = [
            self.cli, "agent", "--local", "--agent", self.agent_id,
            "--session-key", key, "--json", "-m", text,
        ]
        start = time.monotonic()
        attempts = 3
        proc = None
        for attempt in range(1, attempts + 1):
            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout or self.turn_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise AdapterTimeout(f"openclaw 单轮超时（{timeout or self.turn_timeout}s）") from exc
            if proc.returncode == 0:
                break
            combined = f"{proc.stderr or ''}\n{proc.stdout or ''}"
            # 并发时机器级租约（gateway state ownership）会在启动期瞬时争抢：
            # 零副作用，可安全重试（CONTRACT §8「首轮零副作用传输类失败」口径）
            if "gateway state ownership" in combined and attempt < attempts:
                time.sleep(3)
                continue
            raise AdapterError(
                f"openclaw 退出码 {proc.returncode}：{(proc.stderr or proc.stdout or '')[:400]}"
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        assert proc is not None

        data = _extract_json(proc.stdout or "")
        reply = ""
        model = usage = sid = None
        if data:
            reply = "".join(
                str(p.get("text", "")) for p in data.get("payloads", []) if isinstance(p, dict)
            )
            meta = (data.get("meta") or {}).get("agentMeta") or {}
            model = meta.get("model")
            usage = meta.get("usage")
            sid = meta.get("sessionId")
        if proc.returncode != 0:
            raise AdapterError(
                f"openclaw 退出码 {proc.returncode}：{(proc.stderr or proc.stdout or '')[:400]}"
            )
        return TurnResult(
            reply_text=reply.strip(),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            session_id=sid,
            model=model,
            usage=usage,
        )

    # ---- 时间锚定归一化（I-15 方案 2） ----

    def normalize_time_memory(self, anchor: str, session_start_real) -> dict:
        """重写每日记忆文件名/条目时间戳 + USER.md/MEMORY.md 的 observed 元数据与 mtime，并重建索引。"""
        anchor_dt = parse_anchor(anchor)
        result = normalize_openclaw_memory(self.workspace / "memory", anchor_dt, session_start_real)
        real_date = session_start_real.strftime("%Y-%m-%d")
        anchor_date = anchor_dt.strftime("%Y-%m-%d")
        core_files: list[dict] = []
        for name in _BASELINE_FILES:
            path = self.workspace / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            new_text, count = rewrite_observed_dates(text, real_date, anchor_date)
            changed_now = path.stat().st_mtime >= session_start_real.timestamp() - 5
            if count:
                path.write_text(new_text, encoding="utf-8", newline="\n")
            if count or changed_now:
                os.utime(path, (anchor_dt.timestamp(), anchor_dt.timestamp()))
            core_files.append(
                {"file": name, "observed_rewritten": count, "mtime_set": bool(count or changed_now)}
            )
        result["core_files"] = core_files
        if result["renamed"] or any(item["observed_rewritten"] for item in core_files):
            index = self._run_with_retry(["memory", "index"])
            result["index"] = {"ok": index["ok"], "returncode": index["returncode"]}
        return result

    # ---- 记忆快照 ----

    def probe_memory(self, phase: str, *, for_task_id: str | None = None) -> MemorySnapshot:
        started_at = now_iso()
        entries = []
        files = 0
        total_bytes = 0
        sources: list[str] = []
        from engine.memory_capture import capture_files
        capture = capture_files(self.workspace, ['MEMORY.md', 'USER.md', 'memory/*.md'])
        for doc in capture['documents']:
            source = doc['path']
            text = doc['content'].replace('\r\n', '\n').replace('\r', '\n')
            files += 1
            total_bytes += len(text.encode("utf-8"))
            stamp = datetime.datetime.fromtimestamp(doc['source_mtime_ns'] / 1e9).astimezone().isoformat(timespec='seconds')
            entries.extend(parse_markdown_memory(text, source, stamp))
            sources.append(source)
        coverage = {
            "files_scanned": files,
            "entries_scanned": len(entries),
            "bytes_scanned": total_bytes,
            "sources": sources,
        }
        completed_at = now_iso()
        from engine.observation import file_scope
        scope = file_scope(self.workspace, ['MEMORY.md', 'USER.md', 'memory/*.md'],
                           'markdown-v1', started_at, completed_at, full_capture=True)
        return MemorySnapshot(phase=phase, taken_at=completed_at, entries=entries,
                              coverage=coverage, observation=scope, full_capture=capture)

    # ---- CLI 辅助 ----

    def _run_with_retry(self, args: list[str], attempts: int = 2, timeout: float = 120) -> dict:
        """记忆子系统并发时偶发锁超时（实测），失败重试一次。"""
        result = self._run(args, timeout=timeout)
        if not result["ok"] and attempts > 1:
            time.sleep(2)
            result = self._run(args, timeout=timeout)
        return result

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


def _extract_json(stdout: str) -> dict | None:
    """从混合日志的 stdout 里抽出 JSON 对象（`--json` 输出）。"""
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
