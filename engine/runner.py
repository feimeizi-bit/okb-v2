"""okb 引擎 · 批量调度与单 task 生命周期编排（CONTRACT §1 / §8，I-9 / I-10）

用法（VM 内、仓库根目录）：

    # 单样本冒烟
    python3 -m engine.runner --agent openclaw --suite samples/pilot-v0.1.yaml \
        --sample-id boundary_pii_mask_001

    # 批量：两款 Agent × 全部样本 × 2 次重复（串行）
    python3 -m engine.runner --agent openclaw --agent hermes \
        --suite samples/pilot-v0.1.yaml --repeat 2

    # 只看计划与能力预判（不调用 Agent）
    python3 -m engine.runner --agent openclaw --suite samples/pilot-v0.1.yaml --dry-run

状态机（I-9）：ok / timeout / agent_error / env_error / skip；
timeout / agent_error / env_error 记 invalid（报告单列），skip 单独统计。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable

from engine import redact as R
from engine.adapter import (
    AdapterError,
    AdapterTimeout,
    AgentAdapter,
    MemorySnapshot,
    TaskContext,
    safe_config,
)
from engine.collectors import EvidenceWriter, now_iso, sha256_text, truncate_text
from engine.paths import PathNotFound
from engine.timeanchor import parse_anchor, set_mtimes
from engine.workspace import FileInfo, PathSafetyError, Workspace, sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ANCHOR_TYPES = {"memory_entry", "memory_read", "memory_write"}
INVALID_STATUSES = ("timeout", "agent_error", "env_error")
STOP_EXIT_CODE = 3
SIGNAL_EXIT_CODE = 130


class BatchStop(Exception):
    """批次停止请求（信号 / 外部控制）：当前任务回滚，未跑任务不记，可续跑。"""


class RunLogger:
    """`runs/<id>/run.log`（JSONL，长跑可 tail）+ stdout 人读行。"""

    def __init__(self, run_dir: Path, echo: bool = True):
        self.path = Path(run_dir) / "run.log"
        self.echo = echo
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        ts = now_iso()
        record = {"ts": ts, "event": event, **fields}
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.echo:
            extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
            try:
                print(f"[{ts}] {event} {extra}".rstrip(), flush=True)
            except (BrokenPipeError, OSError):
                # console 管道断开（会话被撕/tee 死亡）时静默：run.log 已落盘，跑批继续
                self.echo = False


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # Windows：os.kill(pid, 0) 会发 Ctrl+C，改用 OpenProcess 探测
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class RunLock:
    """run 目录写锁：防双进程写同一 run；死进程的陈旧锁可接管。"""

    def __init__(self, run_dir: Path, logger: RunLogger | None = None):
        self.path = Path(run_dir) / ".lock"
        self.logger = logger
        self.acquired = False

    def acquire(self, run_id: str) -> dict:
        if self.path.exists():
            try:
                stale = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                stale = {}
            pid = int(stale.get("pid", -1))
            same_host = str(stale.get("host", "")) == socket.gethostname()
            if same_host and _pid_alive(pid):
                raise RuntimeError(
                    f"run 目录已被进程占用（pid={pid}, host={stale.get('host')}）：{self.path}；"
                    "如需续跑请等对方退出或使用 --resume"
                )
            if self.logger:
                self.logger.log("lock_stale", path=str(self.path), old_pid=pid)
        self.path.write_text(
            json.dumps(
                {"run_id": run_id, "pid": os.getpid(), "host": socket.gethostname(), "acquired_at": now_iso()},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self.acquired = True
        return {"pid": os.getpid()}

    def release(self) -> None:
        if self.acquired and self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass
        self.acquired = False


class StopFlag:
    """信号/守卫共用的停止请求位。"""

    def __init__(self) -> None:
        self.requested = False
        self.reason: str | None = None

    def request(self, reason: str) -> None:
        self.requested = True
        self.reason = reason


class CircuitBreaker:
    """连续 invalid 熔断（ok 重置，skip 不计）。"""

    def __init__(self, threshold: int = 3):
        self.threshold = max(0, int(threshold))
        self.consecutive_invalid = 0

    def record(self, status: str) -> bool:
        if status == "ok":
            self.consecutive_invalid = 0
        elif status in INVALID_STATUSES:
            self.consecutive_invalid += 1
        return bool(self.threshold) and self.consecutive_invalid >= self.threshold


# ---------- 任务台账（长跑保障：断点续跑 / 崩溃取证） ----------


def ledger_append(run_dir: Path, record: dict) -> None:
    path = Path(run_dir) / "tasks.jsonl"
    record = {"ts": now_iso(), **record}
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def ledger_read(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "tasks.jsonl"
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def resume_state(ledger: list[dict], plan: list[dict], retry_invalid: bool = False) -> dict:
    """按台账把计划分成 done / inflight / pending。

    - done：有 finish 行（终态）；`--retry-invalid` 时 invalid 终态重开为 pending
    - inflight：只有 start 行（崩溃/停机在任务中）→ 续跑前回滚到 start 行的 checkpoint
    - pending：无记录
    """
    last: dict[str, dict] = {}
    for rec in ledger:
        tid = rec.get("task_id")
        if tid:
            last[tid] = rec
    done: dict[str, dict] = {}
    inflight: dict[str, dict] = {}
    pending: list[dict] = []
    retry: set[str] = set()
    for item in plan:
        tid = item["task_id"]
        rec = last.get(tid)
        if rec is None:
            pending.append(item)
        elif rec.get("event") == "finish":
            if retry_invalid and rec.get("status") in INVALID_STATUSES:
                pending.append(item)
                retry.add(tid)
            else:
                done[tid] = rec
        else:
            inflight[tid] = rec
    return {"done": done, "inflight": inflight, "pending": pending, "retry": retry}


def ledger_summary(rec: dict) -> dict:
    """从 finish 台账行还原 manifest.tasks 条目。"""
    return {
        "task_id": rec.get("task_id"),
        "status": rec.get("status"),
        "reason": rec.get("reason"),
        "duration_ms": rec.get("duration_ms", 0),
        "counts": rec.get("counts", {}),
        "usage": rec.get("usage"),
        "attempts": rec.get("attempts", 1),
    }


def usage_tokens(usage: dict | None) -> int:
    """token 用量估算：total_tokens 优先，否则 input/output 或 prompt/completion 求和。"""
    if not usage:
        return 0
    for key in ("total_tokens", "totalTokens", "total"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    total = 0
    for key in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def free_mb(path: str | Path) -> float:
    return shutil.disk_usage(str(path)).free / (1024 * 1024)


def slice_plan(plan: list[dict], index: int, total: int) -> list[dict]:
    """确定性轮转切片（index 1-based）：多机/多进程分片跑批。"""
    if total <= 1:
        return list(plan)
    return [item for i, item in enumerate(plan) if i % total == index - 1]


def write_preflight_report(
    run_dir: Path,
    adapters: list[AgentAdapter],
    *,
    min_free_mb: float = 512.0,
    paper_path: str | Path | None = None,
    expect_paper_sha: str | None = None,
) -> dict:
    """跑前检查汇总（磁盘 / paper sha / 各适配器 health），写 preflight.json。"""
    checks: list[dict] = []
    disk = free_mb(run_dir if Path(run_dir).exists() else Path(run_dir).parent)
    checks.append(
        {
            "name": "disk_free_mb",
            "ok": disk >= min_free_mb,
            "detail": f"{disk:.0f} MB（阈值 {min_free_mb:.0f} MB）",
        }
    )
    if expect_paper_sha:
        actual = sha256_file(Path(paper_path)) if paper_path and Path(paper_path).is_file() else None
        checks.append(
            {
                "name": "paper_sha256",
                "ok": actual == expect_paper_sha,
                "detail": actual or "paper 不存在",
            }
        )
    for adapter in adapters:
        health = adapter.health()
        for check in health["checks"]:
            checks.append(
                {"name": f"{adapter.name}:{check['name']}", "ok": check["ok"], "detail": check.get("detail", "")}
            )
    report = {"ts": now_iso(), "ok": all(c["ok"] for c in checks), "checks": checks}
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return report


def load_suite(path: str | Path):
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from loader import load_yaml  # type: ignore

    return load_yaml(path)


def load_paper(path: str | Path):
    """读 `build/paper.jsonl`（B 的构建产物，官方跑批输入）：一行一个样本对象。

    与 B 的评分器 / C-2 工具链同源（都消费 paper.jsonl），保证 suite 版本可锁（sha 登记）。
    """
    samples: list[dict] = []
    suite_version: str | None = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        sv = (sample.get("meta") or {}).get("suite_version")
        if suite_version is None and sv:
            suite_version = str(sv)
        samples.append(sample)
    if not samples:
        raise SystemExit(f"paper 为空：{path}")
    return suite_version, samples


def make_adapter(name: str, config: dict | None = None) -> AgentAdapter:
    if name == "openclaw":
        from engine.adapters.openclaw import OpenClawAdapter

        return OpenClawAdapter(config)
    if name == "hermes":
        from engine.adapters.hermes import HermesAdapter

        return HermesAdapter(config)
    if name == "mock":
        from engine.adapters.mock import MockAdapter

        return MockAdapter(config)
    if name == "cli":
        # 声明式 CLI 适配器：第三个及以后的智能体（KylinBot / kylin-agent 等）靠配置接入，
        # 不在仓里猜它们的 argv。缺配置直接报错，不降级成"假装能跑"。
        from engine.adapters.cli_template import CliTemplateAdapter

        return CliTemplateAdapter(config)
    raise SystemExit(f"未知 agent：{name}（可用：openclaw / hermes / mock / cli）")


# ---------- 计划与汇总（纯函数，可单测） ----------


def duplicate_agent_names(names: list[str]) -> list[str]:
    """ISS-11：重复的 --agent 名。task_id 仅由 agent 名派生 → 同名会混流同名任务、
    覆写 raw/<task_id>/（rc=0 的静默数据损坏）。多实例请分次跑批（各自 --run-dir）后归并。"""
    seen: set[str] = set()
    dup: set[str] = set()
    for name in names:
        if name in seen:
            dup.add(name)
        seen.add(name)
    return sorted(dup)


def plan_tasks(agents: list[str], samples: list[dict], repeat: int = 1) -> list[dict]:
    """展开 agents × samples × repeats 为 task 计划（串行执行顺序）。"""
    plan: list[dict] = []
    for agent in agents:
        for sample in samples:
            for rep in range(1, repeat + 1):
                task_id = f"{agent}-{sample['id']}" + (f"-r{rep}" if repeat > 1 else "")
                plan.append(
                    {
                        "task_id": task_id,
                        "agent": agent,
                        "sample_id": sample["id"],
                        "repeat": rep,
                    }
                )
    return plan


def summarize(tasks: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    for task in tasks:
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
    invalid = sum(by_status.get(status, 0) for status in INVALID_STATUSES)
    return {
        "total": len(tasks),
        "by_status": by_status,
        "invalid": invalid,
        "skipped": by_status.get("skip", 0),
    }


# ---------- 能力预判（CONTRACT §7：requires 从字段派生） ----------


def derive_requires(sample: dict) -> set[str]:
    req: set[str] = set()
    if sample.get("time_anchor") or any(s.get("time") for s in sample.get("script", [])):
        req.add("time_anchor")
    if sample.get("fixture"):
        req.add("file")
    for anchor in sample.get("anchors", []):
        atype = anchor.get("type")
        if atype in MEMORY_ANCHOR_TYPES:
            req.add("memory")
        elif atype == "tool_call":
            req.add("tool")
        elif atype == "file_content":
            req.add("file")
    return req


def check_capabilities(sample: dict, caps) -> list[str]:
    missing: list[str] = []
    for req in sorted(derive_requires(sample)):
        if req == "time_anchor":
            if not caps.supports_time_anchor:
                missing.append("time_anchor（Agent 不支持时间锚定）")
        elif not getattr(caps, req, False):
            missing.append(req)
    return missing


# ---------- 单 task 生命周期（CONTRACT §1） ----------


def _read_contents(ws: Workspace, snap: dict[str, FileInfo], max_bytes: int = 1024 * 1024) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel, info in snap.items():
        if info.size > max_bytes:
            continue
        text = ws.read_text(rel, max_bytes=max_bytes)
        if text is not None:
            out[rel] = text
    return out


def run_task(
    *,
    adapter: AgentAdapter,
    sample: dict,
    run_id: str,
    run_dir: Path,
    writer: EvidenceWriter,
    work_root: Path,
    plan: dict | None = None,
    timeout: float | None = None,
    task_timeout: float = 900.0,
    stop_check: Callable[[], bool] | None = None,
    watch: Callable[[], list[dict]] | None = None,
    watch_interval: float = 300.0,
) -> dict:
    task_id = plan["task_id"] if plan else f"{adapter.name}-{sample['id']}"
    sessions = [s["session"] for s in sample.get("script", [])]
    ctx = TaskContext(
        run_id=run_id,
        task_id=task_id,
        workspace=Path(work_root) / task_id,
        sessions=sessions,
        time_anchor=sample.get("time_anchor"),
        sample=sample,
    )
    ws = Workspace(adapter.file_root(ctx), ignore_top=adapter.workspace_ignore())
    pii = R.PiiPipeline(R.load_pii(sample.get("pii", [])))
    counts: dict[str, int] = {}
    models: set[str] = set()
    usage_total: dict[str, int] = {}
    started = time.monotonic()
    deadline = started + task_timeout
    last_watch = started
    status = "ok"
    reason: str | None = None

    def emit(**kwargs) -> dict:
        line = writer.emit(task_id=task_id, pii=pii, **kwargs)
        counts[line["type"]] = counts.get(line["type"], 0) + 1
        return line

    def sysrow(action: str, **extra) -> None:
        payload = {"action": action, **extra}
        raw = writer.raw.write_jsonl(task_id, f"system_{action}.jsonl", [payload])
        emit(source="system", type="message", actor="env", payload=payload, raw_ref=raw)

    def emit_snapshot(snap: MemorySnapshot) -> None:
        ev_id, _ = writer.peek_next()
        redacted: list[dict] = []
        hits: list[dict] = []
        for i, entry in enumerate(snap.entries):
            row = entry.as_dict()
            for field in ("key", "value"):
                text, found = pii.process(row[field], f"payload.entries[{i}].{field}", "memory", ev_id)
                row[field] = text
                hits.extend(found)
            redacted.append(row)
        raw = writer.raw.write_jsonl(task_id, f"memory_{snap.phase}.jsonl", redacted)
        emit(
            source="memory",
            type="memory_read",
            actor="env",
            payload={
                "phase": snap.phase,
                "coverage": snap.coverage,
                "entries_count": len(redacted),
                "via": "probe",
            },
            raw_ref=raw,
            postprocess=lambda line: line["pii_hits"].extend(hits),
            ev_id=ev_id,
        )
        if snap.observation is not None:
            capture_ref = None
            if snap.full_capture is not None:
                from engine.memory_capture import redact_capture
                capture_id, _ = writer.peek_next()
                capture, capture_hits = redact_capture(snap.full_capture, pii, capture_id)
                capture_row = emit(source='system', type='message', actor='env',
                    payload=dict(action='memory_documents', phase=snap.phase, snapshot_ref=ev_id,
                                 instance_id=snap.observation['instance_id'], scope_id=snap.observation['scope_id'],
                                 capture=capture), ev_id=capture_id,
                    postprocess=lambda line: line['pii_hits'].extend(capture_hits))
                capture_ref = capture_row['id']
            from engine.observation import partial_receipt
            receipt = partial_receipt(snap.observation, run_id=run_id, task_id=task_id,
                                      phase=snap.phase, evidence_id=ev_id, item_count=len(redacted))
            payload = dict(action='observation_receipt', receipt=receipt, scope_metadata=snap.observation)
            if capture_ref is not None:
                payload['full_capture_ref'] = capture_ref
            receipt_raw = writer.raw.write_jsonl(task_id, f'observation_receipt_{snap.phase}.jsonl', [payload])
            emit(source='system', type='message', actor='env', payload=payload, raw_ref=receipt_raw)

    def emit_snapshot_failure(phase: str, exc: Exception) -> None:
        """快照失败也落一条带 error 的证据（ISS-07），随后由调用方抛错记 env_error。

        三特征：`payload.error` 非空 + `entries: []`（raw 快照显式空）+ task `env_error`。
        """
        detail = f"{type(exc).__name__}: {exc}"
        raw = writer.raw.write_jsonl(
            task_id,
            f"memory_{phase}_error.jsonl",
            [{"phase": phase, "error": detail, "entries": []}],
        )
        emit(
            source="memory",
            type="memory_read",
            actor="env",
            payload={
                "phase": phase,
                "coverage": {"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0, "sources": []},
                "entries_count": 0,
                "via": "probe",
                "error": detail,
            },
            raw_ref=raw,
        )

    def emit_memory_diff(before: MemorySnapshot, after: MemorySnapshot) -> None:
        old = {(e.source, e.key): e for e in before.entries}
        for entry in after.entries:
            prev = old.get((entry.source, entry.key))
            if prev is not None and prev.value == entry.value:
                continue
            emit(
                source="memory",
                type="memory_write",
                actor="agent",
                payload={
                    "key": entry.key,
                    "value": entry.value,
                    "written_at": entry.written_at,
                    "written_at_source": entry.written_at_source,
                    "source": entry.source,
                    "via": "probe_diff",
                },
                surface="memory",
            )

    def emit_file_change(change, content_before: dict[str, str], content_after: dict[str, str]) -> None:
        payload = {
            "path": change.path,
            "op": change.op,
            "before_content": content_before.get(change.path),
            "before_sha256": change.before.sha256 if change.before else None,
            "after_content": content_after.get(change.path),
            "content_sha256": sha256_text(""),
            "full_bytes": 0,
            "stored_bytes": 0,
            "truncated": False,
        }

        def postprocess(line: dict) -> None:
            row = line["payload"]
            full = row.get("after_content") or ""
            row["content_sha256"] = sha256_text(full)
            row["full_bytes"] = len(full.encode("utf-8"))
            stored, truncated, _, stored_bytes = truncate_text(full)
            if row.get("after_content") is not None:
                row["after_content"] = stored
            row["truncated"] = truncated
            row["stored_bytes"] = stored_bytes
            before = row.get("before_content")
            if before:
                row["before_content"], _, _, _ = truncate_text(before)
            # 物理字节域指纹（脱敏/截断前的原文）：workspace listing 记的是磁盘字节，
            # 只能与同域指纹比对（spec §3：物理字节与脱敏文本指纹域不得混用）。
            # `payload` 是调用方传入的原始 dict（emit 内部对副本做脱敏），未被动过。
            raw_after = payload.get("after_content")
            if isinstance(raw_after, str):
                disk_bytes = len(raw_after.encode("utf-8"))
                disk_sha = sha256_text(raw_after)
                if raw_after != full or truncated:
                    row["disk_sha256"] = disk_sha
                    row["disk_bytes"] = disk_bytes

        emit(source="file", type="file_write", actor="agent", payload=payload, surface="file", postprocess=postprocess)

    try:
        health = adapter.health()
        sysrow("health_before", **health)
        if not health["ok"]:
            bad = "、".join(c["name"] for c in health["checks"] if not c["ok"])
            raise RuntimeError(f"任务前健康检查失败：{bad}")

        caps = adapter.capabilities()
        missing = check_capabilities(sample, caps)
        if missing:
            status = "skip"
            reason = "能力缺失：" + "、".join(missing)
            sysrow("skip", status=status, reason=reason, capabilities=caps.as_dict())
            return _finish(writer, task_id, status, reason, started, counts, extra={"models": []})

        # fixture 撞记忆面保留路径 → env_error（CONTRACT §6，ISS-02）
        surface = adapter.memory_surface_paths()
        for item in sample.get("fixture", []) or []:
            rel = str(item.get("path", ""))
            if rel.split("/", 1)[0] in surface:
                raise RuntimeError(f"fixture 命中记忆面保留路径：{rel}（CONTRACT §6）")

        ws.reset(keep=adapter.workspace_keep())
        # CONTRACT §1：reset 必须先于 fixture 布置（ISS-02 修正）
        reset_actions = adapter.reset(ctx)
        for action in reset_actions:
            sysrow("reset", **action)
        failed = [a for a in reset_actions if not a.get("ok", True)]
        if failed:
            raise RuntimeError(f"reset 失败：{failed[0].get('cmd')} rc={failed[0].get('returncode')}")

        created = ws.materialize(sample.get("fixture", []), pii)
        sysrow("fixture", files=created)
        first_anchor = sample.get("time_anchor") or next(
            (s.get("time") for s in sample.get("script", []) if s.get("time")), None
        )
        if first_anchor and created:
            set_mtimes(ws.root, created, parse_anchor(first_anchor))

        try:
            pre = adapter.probe_memory("before", for_task_id=ctx.task_id)
        except Exception as exc:  # noqa: BLE001 —— 快照失败落 error 证据后按 env_error 处理（ISS-07）
            emit_snapshot_failure("before", exc)
            raise
        emit_snapshot(pre)
        content_before = _read_contents(ws, ws.snapshot())
        before_snap = ws.snapshot()

        prev_snap = before_snap
        for script in sample.get("script", []):
            session = script["session"]
            session_anchor = script.get("time") or ctx.time_anchor
            session_started_real = datetime.datetime.now().astimezone()
            first_turn = True
            for turn_index, turn in enumerate(script.get("turns", [])):
                if stop_check is not None and stop_check():
                    raise BatchStop("收到停止请求（当前任务已回滚）")
                if watch is not None and time.monotonic() - last_watch >= watch_interval:
                    last_watch = time.monotonic()
                    checks = watch()
                    bad = [c for c in checks if not c.get("ok")]
                    if bad:
                        sysrow("watchdog", checks=checks, ok=False)
                        raise RuntimeError(
                            "看门狗：运行环境异常 " + "、".join(str(c.get("name")) for c in bad)
                        )
                if time.monotonic() > deadline:
                    raise AdapterTimeout(f"单任务超时（{task_timeout:.0f}s）")
                text = R.materialize(str(turn.get("text", "")), pii.entries)
                prefix = None
                injected = False
                if first_turn and session_anchor:
                    prefix = f"（当前时间：{session_anchor}）"
                    text = prefix + "\n" + text
                    injected = True
                request_ev_id, _ = writer.peek_next()
                redacted_text, request_hits = pii.process(text, "payload.text", "reply", request_ev_id)
                request_ref = writer.raw.write_text(
                    task_id, f"{session}_t{turn_index}_request.txt", redacted_text
                )
                emit(
                    source="dialogue",
                    type="message",
                    actor="user",
                    session_id=session,
                    turn=turn_index + 1,
                    payload={"text": redacted_text, "injected_prefix": injected, "prefix_text": prefix},
                    raw_ref=request_ref,
                    postprocess=lambda line, hs=request_hits: line["pii_hits"].extend(hs),
                    ev_id=request_ev_id,
                )
                result = adapter.send(ctx, session, turn_index, text, first_turn, timeout=timeout)
                first_turn = False
                if result.model:
                    models.add(str(result.model))
                if result.usage:
                    for usage_key, usage_value in result.usage.items():
                        if isinstance(usage_value, (int, float)) and not isinstance(usage_value, bool):
                            usage_total[usage_key] = usage_total.get(usage_key, 0) + int(usage_value)
                # 记忆写事件（mock 等实时采集方）：走统一采集器落 memory_write 证据（via=memory_api）
                for event in result.memory_events or []:
                    emit(
                        source="memory",
                        type="memory_write",
                        actor="agent",
                        session_id=session,
                        turn=turn_index + 1,
                        payload={
                            "key": event.get("key", ""),
                            "value": event.get("value", ""),
                            "written_at": None,
                            "written_at_source": None,
                            "source": "MEMORY.md",
                            "via": "memory_api",
                        },
                        surface="memory",
                    )
                reply_ev_id, _ = writer.peek_next()
                redacted_reply, reply_hits = pii.process(result.reply_text, "payload.text", "reply", reply_ev_id)
                redacted_stdout, out_hits = pii.process(result.stdout, "raw.stdout", "reply", reply_ev_id)
                redacted_stderr, err_hits = pii.process(result.stderr, "raw.stderr", "reply", reply_ev_id)
                stdout_ref = writer.raw.write_text(
                    task_id, f"{session}_t{turn_index}_stdout.txt", redacted_stdout
                )
                writer.raw.write_text(task_id, f"{session}_t{turn_index}_stderr.txt", redacted_stderr)
                all_hits = reply_hits + out_hits + err_hits
                emit(
                    source="dialogue",
                    type="message",
                    actor="agent",
                    session_id=session,
                    turn=turn_index + 1,
                    payload={"text": redacted_reply, "model": result.model, "usage": result.usage},
                    raw_ref=stdout_ref,
                    postprocess=lambda line, hs=all_hits: line["pii_hits"].extend(hs),
                    ev_id=reply_ev_id,
                )

            # 会话结束后：按锚点归一化时间戳（I-15 方案 2）
            if session_anchor:
                current_snap = ws.snapshot()
                changed = [c.path for c in ws.diff(prev_snap, current_snap) if c.op != "delete"]
                mtime_count = set_mtimes(ws.root, changed, parse_anchor(session_anchor))
                memory_result = adapter.normalize_time_memory(session_anchor, session_started_real)
                sysrow(
                    "time_normalization",
                    session=session,
                    anchor=session_anchor,
                    workspace_files=mtime_count,
                    memory=memory_result,
                )
                prev_snap = current_snap

        try:
            post = adapter.probe_memory("after", for_task_id=ctx.task_id)
        except Exception as exc:  # noqa: BLE001
            emit_snapshot_failure("after", exc)
            raise
        emit_snapshot(post)
        emit_memory_diff(pre, post)

        content_after = _read_contents(ws, ws.snapshot())
        after_snap = ws.snapshot()
        for change in ws.diff(before_snap, after_snap):
            emit_file_change(change, content_before, content_after)

        listing_ref = writer.raw.write_jsonl(task_id, "workspace_listing.jsonl", ws.listing())
        emit(
            source="system",
            type="message",
            actor="env",
            payload={"action": "workspace_listing", "files": len(after_snap)},
            raw_ref=listing_ref,
        )

        health_after = adapter.health()
        sysrow("health_after", **health_after)
        if not health_after["ok"]:
            bad = "、".join(c["name"] for c in health_after["checks"] if not c["ok"])
            status, reason = "env_error", f"任务后健康检查失败：{bad}"
    except BatchStop as exc:
        exc.partial_summary=dict(duration_ms=int((time.monotonic()-started)*1000),counts=dict(counts),
                                 models=sorted(models),usage=usage_total or None)
        raise
    except AdapterTimeout as exc:
        status, reason = "timeout", str(exc)
    except AdapterError as exc:
        status, reason = "agent_error", str(exc)
    except (PathSafetyError, R.UnknownPlaceholderError, PathNotFound, R.PiiError) as exc:
        status, reason = "env_error", f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 —— 未预期异常按 env_error 记录，不判负
        status, reason = "env_error", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            ws.cleanup(keep=adapter.workspace_keep())
        except Exception as exc:  # noqa: BLE001
            sysrow("cleanup_failed", error=f"{type(exc).__name__}: {exc}")

    return _finish(
        writer,
        task_id,
        status,
        reason,
        started,
        counts,
        extra={"models": sorted(models), "usage": usage_total or None},
    )


def _finish(
    writer: EvidenceWriter,
    task_id: str,
    status: str,
    reason: str | None,
    started: float,
    counts: dict,
    extra: dict | None = None,
) -> dict:
    duration_ms = int((time.monotonic() - started) * 1000)
    summary = {
        "task_id": task_id,
        "status": status,
        "reason": reason,
        "duration_ms": duration_ms,
        "counts": counts,
    }
    if extra:
        summary.update(extra)
    payload = {"action": "task_summary", **summary}
    raw = writer.raw.write_jsonl(task_id, "system_task_summary.jsonl", [payload])
    writer.emit(
        task_id=task_id,
        source="system",
        type="message",
        actor="env",
        payload=payload,
        raw_ref=raw,
    )
    return summary


# ---------- 批量调度 ----------


def module_version(module_name: str) -> str:
    """适配器/执行器的版本 = 模块源码 sha256 前 12 位（可比对、不虚构）。"""
    import importlib
    import hashlib

    try:
        mod = importlib.import_module(module_name)
        digest = hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest()[:12]
    except Exception:  # noqa: BLE001 —— 取不到源码就明确 unknown，不编版本号
        digest = "unknown"
    return f"{module_name.split('.')[-1]}@{digest}"


def observation_scope(adapter, **_kw) -> dict:
    """本次观测面的声明：记忆面路径 + 文件根 + 工作区忽略项，全部来自适配器接口。"""
    scope = {"memory_surface_paths": sorted(str(p) for p in (adapter.memory_surface_paths() or set())),
             "workspace_ignore": sorted(str(p) for p in (adapter.workspace_ignore() or set())),
             "memory_capture": bool(getattr(adapter, "agent_kind", None) in ("real", "cli-template"))}
    try:
        import tempfile

        probe = Path(tempfile.gettempdir()) / "okb-scope-probe"
        root = adapter.file_root(TaskContext(run_id="probe", task_id="probe",
                                             workspace=probe, sessions=[]))
        try:
            scope["file_root_relative"] = Path(root).name        # 只记名字，不把本机绝对路径写进 manifest
        except TypeError:
            scope["file_root_relative"] = None
    except Exception:  # noqa: BLE001
        scope["file_root_relative"] = None
    return scope


def init_manifest(run_dir: Path, run_id: str, suite_version: str | None, adapters: list[AgentAdapter], *, retained_run=False) -> dict:
    manifest = {
        "run_id": run_id,
        "schema_version": "evidence.schema.json/v0.1",
        "contract_version": "v0.1",
        "suite_version": suite_version,
        "started_at": now_iso(),
        "finished_at": None,
        "agents": [
            {
                "name": adapter.name,
                "agent_kind": adapter.agent_kind,
                "model": adapter.model_name,
                "temperature": adapter.temperature,
                "capabilities": adapter.capabilities().as_dict(),
                # multirun 的 identity() 要求 adapter_version / executor_version /
                # observation_scope 三项；此前真实 manifest 一个都没有 ⇒ 预登记与汇总链对真实
                # 跑批从未真正可用（实测 run_v02_d1 的 model/temperature 均为 null 且缺这些字段）。
                # 只**补缺**，绝不覆盖适配器自己声明的值（显式声明优先于派生）。
                "config": {**{"adapter_version": module_version(adapter.__class__.__module__),
                              "executor_version": module_version(__name__),
                              "observation_scope": observation_scope(adapter)},
                            **safe_config(adapter.config)},
                "config_files": adapter.config_fingerprint(),
                "event_coverage": adapter.event_coverage(),
            }
            for adapter in adapters
        ],
        "tasks": [],
    }
    if retained_run:
        from engine.runtime_identity import capture
        manifest['agents']=[capture(a) for a in adapters]
        manifest['attempt_retention']=dict(format='okb-retained-run-v1-draft',automatic_retry=False,
                                           resume_allowed=False,interrupted_evidence_preserved=True)
    _write_manifest(run_dir, manifest)
    return manifest


def _write_manifest(run_dir: Path, manifest: dict) -> None:
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def finalize_manifest(run_dir: Path, manifest: dict, tasks: list[dict]) -> None:
    manifest["finished_at"] = now_iso()
    manifest["tasks"] = tasks
    _write_manifest(run_dir, manifest)


def _adapter_plan(adapter: AgentAdapter, samples: list[dict], repeat: int, shard: tuple[int, int] | None) -> list[dict]:
    plan = plan_tasks([adapter.name], samples, repeat)
    if shard:
        plan = slice_plan(plan, shard[0], shard[1])
    return plan


def run_batch(
    *,
    adapters: list[AgentAdapter],
    samples: list[dict],
    suite_version: str | None,
    run_id: str,
    runs_dir: str | Path = "runs",
    repeat: int = 1,
    turn_timeout: float | None = None,
    task_timeout: float = 900.0,
    run_dir: Path | str | None = None,
    resume: bool = False,
    retry_invalid: bool = False,
    task_retries: int = 1,
    max_consecutive_invalid: int = 3,
    max_tokens: int | None = None,
    min_free_mb: float = 512.0,
    health_interval: float = 300.0,
    shard: tuple[int, int] | None = None,
    stop: StopFlag | None = None,
    paper_path: str | Path | None = None,
    expect_paper_sha: str | None = None,
    echo: bool = False,
    guard_existing: bool = False,
    retained_run: bool = False,
) -> tuple[Path, list[dict]]:
    """批量调度（长跑保障版）。

    新增（全部向后兼容，simulate.py 等旧调用不受影响）：
    - 任务台账 tasks.jsonl + `--resume` 断点续跑（证据回滚到任务起点）
    - 运行锁 / run.log / 优雅停机（StopFlag）/ 熔断 / 任务级重试
    - 预算（max_tokens）与磁盘（min_free_mb）守卫 / 轮间看门狗
    - `--shard I/N` 分片（配合 engine/merge_runs.py 归并）

    程序化调用默认 `echo=False`、`guard_existing=False`（保持旧 stdout 与覆盖语义）；
    CLI 传 True（长跑防误重入）。
    """
    dup_agents = duplicate_agent_names([adapter.name for adapter in adapters])
    if dup_agents:
        raise RuntimeError(
            f"适配器名重复：{dup_agents}——同名 task_id 会混流并覆写 raw/<task_id>/；"
            "多实例请分次跑批（各自 --run-dir）后用 engine.merge_runs 归并"
        )
    stop = stop or StopFlag()
    run_dir = Path(run_dir) if run_dir is not None else Path(runs_dir) / run_id
    if retained_run:
        if resume or retry_invalid or task_retries != 0 or run_dir.exists():
            raise RuntimeError('retained run requires new directory, task_retries=0, no resume/retry-invalid')
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_id):
            raise RuntimeError('retained run needs a safe unique run id')
    run_dir.mkdir(parents=True, exist_ok=not retained_run)
    evidence_path = run_dir / "evidence.jsonl"
    if guard_existing and not resume and evidence_path.exists() and evidence_path.stat().st_size > 0:
        raise RuntimeError(
            f"run 目录已有非空证据（{evidence_path}）：如要续跑请加 --resume，否则换 --run-id"
        )

    logger = RunLogger(run_dir, echo=echo)
    lock = RunLock(run_dir, logger)
    lock.acquire(run_id)
    logger.log(
        "run_start",
        run_id=run_id,
        resume=resume,
        shard=(f"{shard[0]}/{shard[1]}" if shard else None),
    )
    try:
        # CONTRACT §13：A 创建空文件占位，内容由 B 的评分程序写入（ISS-06）
        (run_dir / "verdicts.jsonl").touch(exist_ok=True)
        work_root = run_dir / "work"
        writer = EvidenceWriter(run_id, run_dir)
        manifest = init_manifest(run_dir, run_id, suite_version, adapters, retained_run=retained_run)
        if resume:
            manifest["resumed"] = True
            manifest["resumed_at"] = now_iso()
        if shard:
            manifest["shard"] = {"index": shard[0], "total": shard[1]}
        samples_by_id = {s["id"]: s for s in samples}
        tasks: list[dict] = []
        stopped_reason: str | None = None
        breaker = CircuitBreaker(max_consecutive_invalid)
        tokens_spent = 0
        usage_by_task: dict[str, dict] = {}
        ledger = ledger_read(run_dir) if resume else []

        # 预检：磁盘 / paper sha / 各适配器 health（汇总报告落 preflight.json）
        preflight = write_preflight_report(
            run_dir,
            adapters,
            min_free_mb=min_free_mb,
            paper_path=paper_path,
            expect_paper_sha=expect_paper_sha,
        )
        payload = {"action": "preflight", **preflight}
        raw = writer.raw.write_jsonl("run", "preflight.jsonl", [payload])
        writer.emit(task_id="run", source="system", type="message", actor="env", payload=payload, raw_ref=raw)
        if not preflight["ok"]:
            bad = "、".join(c["name"] for c in preflight["checks"] if not c["ok"])
            stopped_reason = "preflight_failed"
            logger.log("preflight_failed", detail=bad)
            for adapter in adapters:
                for item in _adapter_plan(adapter, samples, repeat, shard):
                    rec = {
                        "event": "finish",
                        "task_id": item["task_id"],
                        "status": "env_error",
                        "reason": f"预检失败：{bad}",
                        "duration_ms": 0,
                        "counts": {},
                        "attempts": 0,
                    }
                    ledger_append(run_dir, rec)
                    tasks.append(ledger_summary(rec))
        else:
            plan_state: list[tuple[AgentAdapter, list[dict], dict]] = []
            for adapter in adapters:
                plan = _adapter_plan(adapter, samples, repeat, shard)
                state = resume_state(ledger, plan, retry_invalid=retry_invalid) if resume else {
                    "done": {},
                    "inflight": {},
                    "pending": plan,
                    "retry": set(),
                }
                plan_state.append((adapter, plan, state))

            # 续跑前置：先把 in-flight 任务回滚到起点（此刻台账偏移仍有效）；
            # 再做 retry-invalid 的证据手术（会整体重编号、改变后续偏移）——顺序不可反
            if resume:
                for _adapter, plan, state in plan_state:
                    for item in plan:
                        task_id = item["task_id"]
                        if task_id in state["inflight"]:
                            rec = state["inflight"][task_id]
                            writer.rollback(
                                {
                                    "offset": rec.get("evidence_offset", 0),
                                    "seq": rec.get("evidence_seq", 0),
                                }
                            )
                            shutil.rmtree(run_dir / "raw" / task_id, ignore_errors=True)
                            logger.log("resume_rollback", task_id=task_id)

            for adapter, plan, state in plan_state:
                for item in plan:
                    if stopped_reason:
                        break
                    task_id = item["task_id"]
                    if task_id in state["done"]:
                        tasks.append(ledger_summary(state["done"][task_id]))
                        logger.log("resume_skip", task_id=task_id, status=state["done"][task_id].get("status"))
                        continue
                    if stop.requested:
                        stopped_reason = stop.reason or "stop"
                        logger.log("batch_stop", reason=stopped_reason)
                        break
                    disk = free_mb(run_dir)
                    if disk < min_free_mb:
                        stopped_reason = "disk_low"
                        logger.log("disk_low", free_mb=round(disk, 1), threshold=min_free_mb)
                        break

                    if task_id in state["retry"]:
                        removed = writer.remove_tasks({task_id})
                        shutil.rmtree(run_dir / "raw" / task_id, ignore_errors=True)
                        logger.log("retry_invalid_cleanup", task_id=task_id, removed_evidence=removed)

                    checkpoint = writer.checkpoint()
                    raw_dir = run_dir / "raw" / task_id
                    attempts = 0
                    summary: dict | None = None
                    stopped_here = False
                    while True:
                        attempts += 1
                        if attempts > 1:
                            writer.rollback(checkpoint)
                            shutil.rmtree(raw_dir, ignore_errors=True)
                            logger.log("task_retry", task_id=task_id, attempt=attempts)
                        ledger_append(
                            run_dir,
                            {
                                "event": "start",
                                "task_id": task_id,
                                "attempt": attempts,
                                "evidence_offset": checkpoint["offset"],
                                "evidence_seq": checkpoint["seq"],
                            },
                        )
                        logger.log("task_start", task_id=task_id, attempt=attempts)
                        try:
                            summary = run_task(
                                adapter=adapter,
                                sample=samples_by_id[item["sample_id"]],
                                run_id=run_id,
                                run_dir=run_dir,
                                writer=writer,
                                work_root=work_root,
                                plan=item,
                                timeout=turn_timeout,
                                task_timeout=task_timeout,
                                stop_check=lambda: stop.requested,
                                watch=adapter.watch_health,
                                watch_interval=health_interval,
                            )
                        except BatchStop as exc:
                            stopped_reason = stop.reason or "signal"
                            if retained_run:
                                summary=_finish(writer,task_id,'env_error','interrupted; partial evidence retained',
                                                time.monotonic(),{},extra={'models':[],'usage':None,'interrupted':True,
                                                'usage_complete':False,
                                                **getattr(exc,'partial_summary',{})})
                                logger.log('task_interrupted_retained',task_id=task_id)
                            else:
                                writer.rollback(checkpoint)
                                shutil.rmtree(raw_dir, ignore_errors=True)
                                logger.log("task_rolled_back", task_id=task_id, reason=stopped_reason)
                                stopped_here = True
                        if stopped_here:
                            break
                        assert summary is not None
                        if summary["status"] in INVALID_STATUSES and attempts <= task_retries:
                            logger.log(
                                "task_invalid_retry",
                                task_id=task_id,
                                status=summary["status"],
                                reason=summary.get("reason"),
                            )
                            continue
                        break
                    if stopped_here:
                        break

                    assert summary is not None
                    finish = {
                        "event": "finish",
                        "task_id": task_id,
                        "status": summary["status"],
                        "reason": summary.get("reason"),
                        "duration_ms": summary.get("duration_ms", 0),
                        "counts": summary.get("counts", {}),
                        "usage": summary.get("usage"),
                        "attempts": attempts,
                    }
                    ledger_append(run_dir, finish)
                    tasks.append({**summary, "attempts": attempts})
                    logger.log(
                        "task_finish",
                        task_id=task_id,
                        status=summary["status"],
                        duration_ms=summary.get("duration_ms", 0),
                        attempts=attempts,
                    )
                    if summary.get("usage"):
                        usage_by_task[task_id] = summary["usage"]
                    tokens_spent += usage_tokens(summary.get("usage"))
                    manifest["tasks"] = tasks
                    _write_manifest(run_dir, manifest)

                    if max_tokens and tokens_spent >= max_tokens:
                        stopped_reason = "budget_exceeded"
                        logger.log("budget_exceeded", tokens=tokens_spent, max_tokens=max_tokens)
                        break
                    if breaker.record(summary["status"]):
                        stopped_reason = "circuit_breaker"
                        logger.log("circuit_breaker", consecutive=breaker.consecutive_invalid)
                        break
                if stopped_reason:
                    break

        # 事件覆盖率以实际采集为准（mock 覆写非零；真实 Agent 为 0）
        for idx, adapter in enumerate(adapters):
            if idx < len(manifest["agents"]):
                manifest["agents"][idx]["event_coverage"] = adapter.event_coverage()
            manifest.update(adapter.manifest_extra())

        manifest["stopped_reason"] = stopped_reason
        manifest["usage"] = {"tokens_total": tokens_spent, "max_tokens": max_tokens, "per_task": usage_by_task}
        (run_dir / "usage.json").write_text(
            json.dumps(
                {"run_id": run_id, "stopped_reason": stopped_reason, **manifest["usage"]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if retained_run:
            from engine.runtime_identity import unchanged
            manifest['runtime_identity_unchanged']={a.name:unchanged(a,manifest['agents'][i]) for i,a in enumerate(adapters)}
            # Even a normal exit does not independently reconcile provider billing.
            manifest['usage_complete']=False if any(t.get('interrupted') for t in tasks) else None
        finalize_manifest(run_dir, manifest, tasks)
        logger.log("run_end", stopped_reason=stopped_reason, tasks=len(tasks), tokens=tokens_spent)
        return run_dir, tasks
    finally:
        lock.release()


# ---------- CLI ----------


def _parse_shard(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    if "/" not in value:
        raise ValueError("--shard 格式应为 I/N，如 1/3")
    left, right = value.split("/", 1)
    index, total = int(left), int(right)
    if total < 1 or not (1 <= index <= total):
        raise ValueError(f"--shard 越界：{value}（要求 1 <= I <= N）")
    return index, total


def resolve_run_dir(
    runs_dir: str | Path,
    run_id: str,
    shard: tuple[int, int] | None,
    override: str | Path | None = None,
) -> Path:
    """run 目录解析：`--run-dir` 覆盖 > 分片后缀 > 默认 `<runs_dir>/<run_id>`。

    覆盖的用途：同一逻辑 run_id 写多个目录（跨产品并行 OpenClaw ∥ Hermes、全量与子集
    分段跑批），跑完由 engine.merge_runs 归并成单目录——证据 id 冲突由归并重编号解决。
    """
    if override:
        return Path(override)
    if shard and shard[1] > 1:
        return Path(runs_dir) / f"{run_id}.shard{shard[0]}of{shard[1]}"
    return Path(runs_dir) / run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine.runner",
        description="okb 批量调度器（agents × samples × repeats，串行；CONTRACT §1/§8）",
    )
    parser.add_argument("--agent", action="append", required=True, choices=["openclaw", "hermes", "mock", "cli"],
                        help="可重复；cli=声明式模板适配器（--agent-config 给命令模板）")
    parser.add_argument("--suite", default=None, help="suite YAML 路径（开发用，如 samples/pilot-v0.1.yaml）")
    parser.add_argument("--paper", default=None, help="build/paper.jsonl 路径（官方跑批用，与 B 工具链同源）")
    parser.add_argument("--mock-policy", default=None, help="mock 策略文件（samples/mock-policies/*.yaml，--agent mock 必填）")
    parser.add_argument("--agent-config", default=None, help="适配器配置覆盖（JSON 字符串，作用于本次所有 --agent；如隔离实例）")
    parser.add_argument("--sample-id", action="append", default=None, help="可重复；缺省跑 suite 内全部")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--turn-timeout", type=float, default=None, help="单轮超时秒数（覆盖适配器默认 180s）")
    parser.add_argument("--task-timeout", type=float, default=900.0, help="单任务超时秒数（默认 900s）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与能力预判，不调用 Agent")
    parser.add_argument("--resume", action="store_true", help="续跑已有 run（按 tasks.jsonl 跳过/重开任务）")
    parser.add_argument('--retained-run',action='store_true',help='新目录单次尝试；须 --task-retries 0；保留中断证据并采集声明配置')
    parser.add_argument("--retry-invalid", action="store_true", help="续跑时把 invalid 终态任务重开（配合 --resume）")
    parser.add_argument("--task-retries", type=int, default=1, help="任务级重试次数（默认 1；重试前回滚证据）")
    parser.add_argument("--max-consecutive-invalid", type=int, default=3, help="连续 invalid 熔断阈值（0=关，默认 3）")
    parser.add_argument("--max-tokens", type=int, default=None, help="token 预算上限（超限优雅停机）")
    parser.add_argument("--min-free-mb", type=float, default=512.0, help="磁盘剩余下限（MB，低于则停机）")
    parser.add_argument("--health-interval", type=float, default=300.0, help="轮间看门狗间隔秒数（默认 300）")
    parser.add_argument("--shard", default=None, help="分片 I/N（如 1/3；配合 engine.merge_runs 归并）")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="覆盖 run 目录（同一逻辑 run_id 多目录：跨产品并行/分段跑批，跑完 merge_runs 归并）",
    )
    parser.add_argument("--preflight-only", action="store_true", help="只做跑前检查（写 preflight.json）后退出")
    parser.add_argument("--expect-paper-sha", default=None, help="校验 paper sha256（防跑错 suite）")
    args = parser.parse_args(argv)

    # ISS-11：同名 agent 会产出同名 task_id（混流 + raw 覆写），CLI 层先挡
    dup_agents = duplicate_agent_names(args.agent)
    if dup_agents:
        parser.error(
            f"--agent 重复：{dup_agents}——同名任务会混流/覆写 raw/；"
            "多实例请分次跑批（各自 --run-dir）后 merge_runs 归并"
        )

    if not args.paper and not args.suite:
        parser.error("需要 --suite 或 --paper 之一")
    try:
        shard = _parse_shard(args.shard)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.resume and not args.run_id:
        parser.error("--resume 需要显式 --run-id")
    if args.paper:
        suite_version, samples = load_paper(args.paper)
    else:
        suite_version, samples = load_suite(args.suite)
    if args.sample_id:
        wanted = set(args.sample_id)
        picked = [s for s in samples if s.get("id") in wanted]
        missing = wanted - {s["id"] for s in picked}
        if missing:
            print(f"样本不存在：{sorted(missing)}", file=sys.stderr)
            return 2
        samples = picked
    if not samples:
        print("没有可运行的样本", file=sys.stderr)
        return 2

    if "mock" in args.agent and not args.mock_policy:
        parser.error("--agent mock 需要 --mock-policy 指定策略文件")

    base_config: dict = {}
    if args.agent_config:
        try:
            base_config = json.loads(args.agent_config)
        except json.JSONDecodeError as exc:
            print(f"--agent-config 不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        if not isinstance(base_config, dict):
            print("--agent-config 必须是 JSON 对象", file=sys.stderr)
            return 2

    adapters: list[AgentAdapter] = []
    for name in args.agent:
        config = dict(base_config)
        if name == "mock":
            config["policy"] = args.mock_policy
        try:
            adapters.append(make_adapter(name, config or None))
        except Exception as exc:  # noqa: BLE001 —— 适配器/策略初始化失败属评测器侧错误
            print(f"适配器初始化失败（{name}）：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    for adapter in adapters:
        try:
            adapter.validate_suite(suite_version, [s["id"] for s in samples])
        except Exception as exc:  # noqa: BLE001
            print(f"策略与样本对位失败（{adapter.name}）：{exc}", file=sys.stderr)
            return 2

    if args.dry_run:
        plan: list[dict] = []
        for adapter in adapters:
            caps = adapter.capabilities()
            for item in _adapter_plan(adapter, samples, args.repeat, shard):
                sample = next(s for s in samples if s["id"] == item["sample_id"])
                miss = check_capabilities(sample, caps)
                item["predicted"] = "skip" if miss else "run"
                if miss:
                    item["missing"] = miss
                plan.append(item)
        print(json.dumps({"suite_version": suite_version, "plan": plan}, ensure_ascii=False, indent=2))
        return 0

    run_id = args.run_id or "run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = resolve_run_dir(args.runs_dir, run_id, shard, args.run_dir)

    if args.preflight_only:
        report = write_preflight_report(
            run_dir,
            adapters,
            min_free_mb=args.min_free_mb,
            paper_path=args.paper,
            expect_paper_sha=args.expect_paper_sha,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    stop = StopFlag()

    def _handle_signal(signum, _frame):
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        if stop.requested:
            raise KeyboardInterrupt
        stop.request(f"signal:{name}")
        print(f"\n[runner] 收到 {name}：当前任务回滚后停止（再按一次强制退出）", file=sys.stderr)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass

    try:
        run_dir, tasks = run_batch(
            adapters=adapters,
            samples=samples,
            suite_version=suite_version,
            run_id=run_id,
            runs_dir=args.runs_dir,
            run_dir=run_dir,
            repeat=args.repeat,
            turn_timeout=args.turn_timeout,
            task_timeout=args.task_timeout,
            resume=args.resume,
            retry_invalid=args.retry_invalid,
            task_retries=args.task_retries,
            max_consecutive_invalid=args.max_consecutive_invalid,
            max_tokens=args.max_tokens,
            min_free_mb=args.min_free_mb,
            health_interval=args.health_interval,
            shard=shard,
            stop=stop,
            paper_path=args.paper,
            expect_paper_sha=args.expect_paper_sha,
            echo=True,
            guard_existing=True,
            retained_run=args.retained_run,
        )
    except RuntimeError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2

    manifest = json.loads((Path(run_dir) / "manifest.json").read_text(encoding="utf-8"))
    stopped_reason = manifest.get("stopped_reason")
    summary = summarize(tasks)

    def _report() -> None:
        for task in tasks:
            line = f"{task['status']:<12} {task['task_id']:<48} {task['duration_ms']:>7}ms"
            if task.get("reason"):
                line += f"  {task['reason']}"
            print(line)
        print(
            f"合计 {summary['total']} · ok={summary['by_status'].get('ok', 0)}"
            f" · skip={summary['skipped']} · invalid={summary['invalid']}"
            f" · 产物 {run_dir}"
        )
        if stopped_reason:
            print(f"停机原因：{stopped_reason}（可 --resume 续跑）", file=sys.stderr)

    try:
        _report()
    except (BrokenPipeError, OSError):
        pass  # console 已断：汇总已在 manifest/usage.json/run.log 中
    if stopped_reason and str(stopped_reason).startswith("signal"):
        return SIGNAL_EXIT_CODE
    if stopped_reason:
        return STOP_EXIT_CODE
    return 1 if summary["invalid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
