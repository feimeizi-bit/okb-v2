"""ISS-07/08 验收单测（A 侧证据面，供 B 侧 P4-4 复验）

- ISS-07：快照失败 → `payload.error` 非空 + `entries: []`（raw）+ task `env_error`
- ISS-08：运行期 embed-server 死亡（health_after 失败 / 轮间看门狗）→ `env_error`
  且证据行/run.log 可指认

用法：python engine/tests/test_iss07_08.py（宿主/VM 均可跑，不依赖 yaml）
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.adapter import Capabilities, MemorySnapshot, TurnResult  # noqa: E402
from engine.collectors import now_iso  # noqa: E402
from engine.runner import run_batch  # noqa: E402


class FunctionalAdapter:
    """可跑通完整 task 生命周期的桩：按场景开关注入失败。"""

    name = "fake"
    agent_kind = "real"
    model_name = None
    temperature = None

    def __init__(self, *, snapshot_fail: bool = False, die_after_send: bool = False, watch_fail: bool = False):
        self.config = {}
        self.snapshot_fail = snapshot_fail
        self.die_after_send = die_after_send
        self.watch_fail = watch_fail
        self.dead = False

    def health(self):
        if self.dead:
            return {"ok": False, "checks": [{"name": "cmd:embed-server", "ok": False, "detail": "inactive"}]}
        return {"ok": True, "checks": [{"name": "cmd:embed-server", "ok": True, "detail": "active"}]}

    def watch_health(self):
        if self.watch_fail:
            return [{"name": "watch:embed-server", "ok": False, "detail": "inactive"}]
        return []

    def capabilities(self):
        return Capabilities(dialogue=True)

    def validate_suite(self, suite_version, sample_ids):
        return None

    def manifest_extra(self):
        return {}

    def event_coverage(self):
        return {"memory_write_via_memory_api": 0, "memory_read_via_agent": 0}

    def config_fingerprint(self):
        return []

    def file_root(self, ctx):
        return ctx.workspace

    def workspace_ignore(self):
        return set()

    def workspace_keep(self):
        return None

    def memory_surface_paths(self):
        return set()

    def reset(self, ctx):
        return [{"cmd": "fake reset", "returncode": 0, "ok": True, "duration_ms": 0}]

    def probe_memory(self, phase, *, for_task_id=None):
        if self.snapshot_fail:
            raise RuntimeError("synthetic: embed-server 快照不可用")
        return MemorySnapshot(
            phase=phase,
            taken_at=now_iso(),
            entries=[],
            coverage={"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0, "sources": []},
        )

    def send(self, ctx, session, turn_index, text, session_first_turn, timeout=None):
        if self.die_after_send:
            self.dead = True
        return TurnResult(reply_text="ok", stdout="", stderr="", exit_code=0, duration_ms=1)

    def normalize_time_memory(self, anchor, session_start_real):
        return {"supported": False}


def evidence_lines(run_dir: pathlib.Path) -> list[dict]:
    text = (run_dir / "evidence.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_one(tmp: pathlib.Path, name: str, adapter: FunctionalAdapter, health_interval: float = 300.0):
    sample = {"id": "s1", "script": [{"session": "s1", "turns": [{"text": "你好"}]}]}
    return run_batch(
        adapters=[adapter],
        samples=[sample],
        suite_version="v0.1",
        run_id=name,
        runs_dir=tmp,
        task_retries=0,
        max_consecutive_invalid=0,
        health_interval=health_interval,
    )


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="okb_iss0708_"))

    # ---- ISS-07：快照失败三特征 ----
    run_dir, tasks = run_one(tmp, "iss07", FunctionalAdapter(snapshot_fail=True))
    lines = evidence_lines(run_dir)
    err_rows = [line for line in lines if line["type"] == "memory_read" and (line["payload"] or {}).get("error")]
    check("ISS-07 任务 env_error", bool(tasks) and tasks[0]["status"] == "env_error")
    check("ISS-07 error 证据行唯一", len(err_rows) == 1)
    if err_rows:
        row = err_rows[0]
        check("ISS-07 payload.error 非空", bool(row["payload"]["error"]))
        check("ISS-07 entries_count=0", row["payload"].get("entries_count") == 0)
        check("ISS-07 via=probe", row["payload"].get("via") == "probe")
        raw_path = run_dir / row["raw_ref"]["path"]
        check("ISS-07 raw 落盘", raw_path.is_file())
        if raw_path.is_file():
            raw = json.loads(raw_path.read_text(encoding="utf-8").splitlines()[0])
            check("ISS-07 raw entries: []", raw.get("entries") == [])
    summary = [line for line in lines if (line["payload"] or {}).get("action") == "task_summary"]
    check("ISS-07 task_summary env_error", bool(summary) and summary[-1]["payload"]["status"] == "env_error")

    # ---- ISS-08a：任务后健康检查失败（embed-server 死亡） ----
    run_dir, tasks = run_one(tmp, "iss08a", FunctionalAdapter(die_after_send=True))
    lines = evidence_lines(run_dir)
    check("ISS-08a 任务 env_error", bool(tasks) and tasks[0]["status"] == "env_error")
    check("ISS-08a reason 指认健康检查", "健康检查" in (tasks[0].get("reason") or ""))
    health_rows = [line for line in lines if (line["payload"] or {}).get("action") == "health_after"]
    check("ISS-08a health_after 证据行", bool(health_rows) and health_rows[-1]["payload"]["ok"] is False)
    run_log = (run_dir / "run.log").read_text(encoding="utf-8")
    check(
        "ISS-08a run.log 可指认",
        '"event": "task_finish"' in run_log and '"status": "env_error"' in run_log,
    )

    # ---- ISS-08b：轮间看门狗（interval=0 强制首轮即检） ----
    run_dir, tasks = run_one(tmp, "iss08b", FunctionalAdapter(watch_fail=True), health_interval=0.0)
    lines = evidence_lines(run_dir)
    check("ISS-08b 任务 env_error", bool(tasks) and tasks[0]["status"] == "env_error")
    check("ISS-08b reason 指认看门狗", "看门狗" in (tasks[0].get("reason") or ""))
    watch_rows = [line for line in lines if (line["payload"] or {}).get("action") == "watchdog"]
    check("ISS-08b watchdog 证据行", bool(watch_rows) and watch_rows[-1]["payload"]["ok"] is False)

    if failures:
        print(f"❌ ISS-07/08 测试未通过：{failures}")
        return 1
    print("✅ ISS-07/08 测试全部通过（快照失败三特征 + 健康死亡/看门狗 env_error 可指认）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
