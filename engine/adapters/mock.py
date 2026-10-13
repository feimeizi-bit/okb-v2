"""okb 引擎 · mock（合成考生）适配器（CONTRACT §14 / ISS-05）。

- **独立子进程**（`engine/adapters/mock_agent.py`）+ stdin/stdout JSONL，禁止 in-process 调用
- 策略解析复用 B 的 `tools/mock_policy.py`（与 `resolve` 语义逐字一致，含特异性排序）
- `memory_ops` / `file_ops` 是**状态变更命令**：适配器在本轮返回前真实应用到记忆面
  （`MEMORY.md`）与文件面（`file_root()`）；写操作作为事件交给 runner 的统一采集器发射
  （`via=memory_api`，支撑「写过又删」可判）
- 非法 op / 缺字段 / 越界路径 → `MockPolicyError`（runner 记 `env_error`，不静默丢弃）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from engine import redact as R
from engine.adapter import (
    AdapterTimeout,
    AgentAdapter,
    Capabilities,
    MemorySnapshot,
    TaskContext,
    TurnResult,
    mtime_iso,
    parse_markdown_memory,
)
from engine.collectors import now_iso
from engine.workspace import resolve_in_root

ROOT = Path(__file__).resolve().parents[2]
MOCK_AGENT = Path(__file__).resolve().parent / "mock_agent.py"


def _display_path(path: str | Path) -> str:
    """展示/落盘的策略路径（ISS-12）：优先仓库相对路径；Windows 跨盘符时 os.path.relpath
    抛 ValueError（C: temp vs D: 仓库）——退回绝对路径，测试与验证环境不再因此变红。"""
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


class MockPolicyError(RuntimeError):
    """策略文件 / ops 侧错误（评测器问题 → env_error）。"""


class MockAdapter(AgentAdapter):
    name = "mock"
    agent_kind = "mock"
    model_name = None
    temperature = None

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        self.policy_path = str(cfg.get("policy") or cfg.get("mock_policy") or "")
        if not self.policy_path:
            raise MockPolicyError("mock 适配器需要策略文件路径（--mock-policy）")
        sys.path.insert(0, str(ROOT / "tools"))
        import mock_policy  # noqa: PLC0415

        try:
            self._policy = mock_policy.Policy.load(self.policy_path)
        except Exception as exc:  # noqa: BLE001
            raise MockPolicyError(f"策略加载失败：{type(exc).__name__}: {exc}") from exc
        self._proc: subprocess.Popen | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._entries: list[dict] = []
        self._root: Path | None = None
        self._pii: R.PiiPipeline | None = None
        self._counts = {"memory_write_via_memory_api": 0, "memory_read_via_agent": 0}
        self._fired: set[tuple[str, int]] = set()
        self.turn_timeout = float(cfg.get("turn_timeout_seconds", 60))

    # ---- 能力与根 ----

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dialogue=True,
            memory=True,
            file=True,
            tool=True,
            notes=f"mock 合成考生（tier={self._policy.tier}）",
            supports_time_anchor=True,
        )

    def file_root(self, ctx: TaskContext) -> Path:
        return Path(ctx.workspace)

    def workspace_ignore(self) -> set[str]:
        return {"MEMORY.md"}

    # ---- 子进程 ----

    def _start(self) -> None:
        self._stop()
        self._proc = subprocess.Popen(
            [sys.executable, str(MOCK_AGENT), "--policy", self.policy_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._pool = ThreadPoolExecutor(max_workers=1)

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.poll() is None and proc.stdin:
                    proc.stdin.write(json.dumps({"type": "bye"}) + "\n")
                    proc.stdin.flush()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        if stream:
                            stream.close()
                    except Exception:  # noqa: BLE001
                        pass
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def _request(self, message: dict, timeout: float) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            err = ""
            if self._proc is not None and self._proc.stderr is not None:
                try:
                    err = self._proc.stderr.read() or ""
                except Exception:  # noqa: BLE001
                    err = ""
            raise MockPolicyError(f"mock 进程未运行（启动失败？）：{err.strip()[:300]}")
        try:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MockPolicyError(f"mock 进程管道中断：{exc}") from exc
        assert self._pool is not None
        future = self._pool.submit(self._proc.stdout.readline)
        try:
            line = future.result(timeout=timeout)
        except FutureTimeout as exc:
            self._stop()
            raise AdapterTimeout(f"mock 单轮超时（{timeout}s）") from exc
        if not line:
            raise MockPolicyError("mock 进程无响应（EOF）")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise MockPolicyError(f"mock 返回非法 JSON：{line[:200]}") from exc

    # ---- 生命周期 ----

    def reset(self, ctx: TaskContext) -> list[dict]:
        self._root = self.file_root(ctx)
        self._entries = []
        self._pii = None
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "MEMORY.md").write_text("", encoding="utf-8", newline="\n")
        self._start()
        return [
            {
                "ok": True,
                "cmd": "mock-reset",
                "policy": _display_path(self.policy_path),
            }
        ]

    def close(self) -> None:
        self._stop()

    # ---- 记忆面 ----

    def _memory_path(self) -> Path | None:
        return self._root / "MEMORY.md" if self._root else None

    def _render_memory(self) -> None:
        path = self._memory_path()
        if path is not None:
            path.write_text(
                "".join(f"- {entry['value']}\n" for entry in self._entries),
                encoding="utf-8",
                newline="\n",
            )

    def probe_memory(self, phase: str, *, for_task_id: str | None = None) -> MemorySnapshot:
        path = self._memory_path()
        text = path.read_text(encoding="utf-8") if path is not None and path.is_file() else ""
        mtime = mtime_iso(path) if path is not None and path.is_file() else ""
        entries = parse_markdown_memory(text, "MEMORY.md", mtime)
        return MemorySnapshot(
            phase=phase,
            taken_at=now_iso(),
            entries=entries,
            coverage={
                "files_scanned": 1 if path is not None and path.is_file() else 0,
                "entries_scanned": len(entries),
                "bytes_scanned": len(text.encode("utf-8")),
                "sources": ["MEMORY.md"],
            },
        )

    # ---- 轮次 ----

    def _pii_for(self, sample: dict) -> R.PiiPipeline:
        if self._pii is None:
            self._pii = R.PiiPipeline(R.load_pii(sample.get("pii", [])))
        return self._pii

    def send(
        self,
        ctx: TaskContext,
        session: str,
        turn_index: int,
        text: str,
        session_first_turn: bool,
        timeout: float | None = None,
    ) -> TurnResult:
        if ctx.sample is None:
            raise MockPolicyError("mock 需要 TaskContext.sample（pii 物化依据）")
        pii = self._pii_for(ctx.sample)

        def materialize(value) -> str:
            return R.materialize(str(value), pii.entries)

        started = time.monotonic()
        response = self._request(
            {
                "type": "turn",
                "run_id": ctx.run_id,
                "task_id": ctx.task_id,
                "session": session,
                "turn": turn_index + 1,
                "message": text,
            },
            timeout or self.turn_timeout,
        )
        if "error" in response:
            raise MockPolicyError(str(response["error"]))
        fired = response.get("fired")
        if isinstance(fired, dict) and "sample_id" in fired and "index" in fired:
            self._fired.add((str(fired["sample_id"]), int(fired["index"])))

        events: list[dict] = []
        for op in response.get("memory_ops") or []:
            kind = op.get("op")
            if kind not in ("set", "append", "delete"):
                raise MockPolicyError(f"非法 memory op：{kind!r}")
            key = materialize(op.get("key", ""))
            value = materialize(op.get("value", ""))
            current = {entry["key"]: idx for idx, entry in enumerate(self._entries)}
            if kind == "set":
                if key in current:
                    self._entries[current[key]]["value"] = value
                else:
                    self._entries.append({"key": key, "value": value})
            elif kind == "append":
                self._entries.append({"key": key, "value": value})
            else:  # delete：不产生写事件；若删掉既有条目，由前后快照 diff 可见
                self._entries = [entry for entry in self._entries if entry["key"] != key]
            if kind in ("set", "append"):
                events.append({"op": kind, "key": key, "value": value})
        self._render_memory()

        for op in response.get("file_ops") or []:
            kind = op.get("op")
            if kind not in ("write", "append", "delete"):
                raise MockPolicyError(f"非法 file op：{kind!r}")
            target = resolve_in_root(self._root, str(op.get("path", "")).replace("\\", "/"))
            if kind == "delete":
                if target.is_file():
                    target.unlink()
                continue
            body = materialize(op.get("content", ""))
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "append" and target.is_file():
                body = target.read_text(encoding="utf-8") + body
            target.write_text(body, encoding="utf-8", newline="\n")

        self._counts["memory_write_via_memory_api"] += len(events)
        return TurnResult(
            reply_text=materialize(response.get("reply_text", "")),
            stdout=json.dumps(
                {
                    "record": {"session": session, "turn": turn_index + 1},
                    "memory_ops": len(response.get("memory_ops") or []),
                    "file_ops": len(response.get("file_ops") or []),
                },
                ensure_ascii=False,
            ),
            stderr="",
            exit_code=0,
            duration_ms=int((time.monotonic() - started) * 1000),
            memory_events=events,
        )

    # ---- 覆盖率与 run 前校验 ----

    def event_coverage(self) -> dict:
        return dict(self._counts)

    def manifest_extra(self) -> dict:
        """manifest 的策略指纹（对齐 B 模拟器输出形状：path/tier/sha256/unused_records）。"""
        import hashlib  # noqa: PLC0415

        unused = [
            {"sample_id": sid, "match": rec.get("match")}
            for sid, recs in self._policy.samples.items()
            for idx, rec in enumerate(recs)
            if (sid, idx) not in self._fired
        ]
        return {
            "policy": {
                "path": _display_path(self.policy_path),
                "tier": self._policy.tier,
                "policy_version": self._policy.raw.get("policy_version"),
                "suite_version": self._policy.suite_version,
                "unused_records": unused,
                "sha256": hashlib.sha256(Path(self.policy_path).read_bytes()).hexdigest(),
            }
        }

    def validate_suite(self, suite_version: str | None, sample_ids: list[str]) -> None:
        import mock_policy  # noqa: PLC0415

        if self._policy.suite_version and suite_version and str(self._policy.suite_version) != str(suite_version):
            raise MockPolicyError(f"策略 suite_version={self._policy.suite_version} ≠ paper {suite_version}")
        uncovered = mock_policy.coverage_guard(self._policy, sample_ids)
        if uncovered:
            raise MockPolicyError(f"策略对以下样本零覆盖（on_missing_record=fail）：{uncovered}")
