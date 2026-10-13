"""engine 长跑保障单测：分片 / 台账续跑 / 证据回滚 / 锁 / 熔断 / usage / 归并

用法：python engine/tests/test_resilience.py（宿主/VM 均可跑）
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.adapter import Capabilities  # noqa: E402
from engine.collectors import EvidenceWriter  # noqa: E402
from engine.merge_runs import merge  # noqa: E402
from engine.runner import (  # noqa: E402
    CircuitBreaker,
    RunLock,
    StopFlag,
    _parse_shard,
    ledger_append,
    ledger_read,
    resolve_run_dir,
    resume_state,
    run_batch,
    slice_plan,
    usage_tokens,
)


class FakeAdapter:
    """最小适配器桩：只验证 run_batch 的守卫路径（不执行任务）。"""

    name = "fake"
    agent_kind = "real"
    model_name = None
    temperature = None

    def __init__(self, healthy: bool = True):
        self.config = {}
        self.healthy = healthy

    def health(self):
        return {"ok": self.healthy, "checks": [] if self.healthy else [{"name": "fake", "ok": False, "detail": "坏"}]}

    def watch_health(self):
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
        raise AssertionError("停止/预检失败路径不应执行任务")

    def probe_memory(self, phase, *, for_task_id=None):
        raise AssertionError("停止/预检失败路径不应执行任务")

    def send(self, *args, **kwargs):
        raise AssertionError("停止/预检失败路径不应执行任务")


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # ---- 分片 ----
    plan = [{"task_id": f"t{i}"} for i in range(10)]
    s0, s1, s2 = (slice_plan(plan, i, 3) for i in (1, 2, 3))
    union = {item["task_id"] for group in (s0, s1, s2) for item in group}
    check("分片并集完整", union == {f"t{i}" for i in range(10)})
    check("分片互斥", len(s0) + len(s1) + len(s2) == 10)
    check("分片均衡", max(len(s0), len(s1), len(s2)) - min(len(s0), len(s1), len(s2)) <= 1)
    check("单分片=全量", slice_plan(plan, 1, 1) == plan)
    check("shard 参数解析", _parse_shard("2/3") == (2, 3) and _parse_shard(None) is None)
    bad_shard = False
    try:
        _parse_shard("4/3")
    except ValueError:
        bad_shard = True
    check("shard 越界拒绝", bad_shard)

    # ---- run 目录解析（--run-dir 覆盖：跨产品并行/分段跑批） ----
    check("默认 run 目录", resolve_run_dir("runs", "run_x", None) == pathlib.Path("runs/run_x"))
    check("分片目录后缀", resolve_run_dir("runs", "run_x", (2, 3)) == pathlib.Path("runs/run_x.shard2of3"))
    check(
        "run-dir 覆盖优先",
        resolve_run_dir("runs", "run_x", (1, 2), "runs/run_x.oc") == pathlib.Path("runs/run_x.oc"),
    )

    # ---- 台账 + 续跑决策 ----
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="okb_res_"))
    run_dir = tmp / "run_test"
    run_dir.mkdir(parents=True)
    ledger_append(run_dir, {"event": "start", "task_id": "a", "attempt": 1, "evidence_offset": 10, "evidence_seq": 1})
    ledger_append(run_dir, {"event": "finish", "task_id": "a", "status": "ok", "duration_ms": 5})
    ledger_append(run_dir, {"event": "start", "task_id": "b", "attempt": 1, "evidence_offset": 99, "evidence_seq": 7})
    ledger_append(run_dir, {"event": "finish", "task_id": "c", "status": "env_error", "duration_ms": 1})
    records = ledger_read(run_dir)
    check("台账读回", len(records) == 4 and records[0]["task_id"] == "a")
    plan3 = [{"task_id": tid} for tid in ("a", "b", "c", "d")]
    state = resume_state(records, plan3)
    check("续跑：done", set(state["done"]) == {"a", "c"})
    check("续跑：inflight 带回滚点", state["inflight"]["b"]["evidence_offset"] == 99)
    check("续跑：pending", [i["task_id"] for i in state["pending"]] == ["d"])
    state2 = resume_state(records, plan3, retry_invalid=True)
    check("retry_invalid 重开 invalid", "c" in [i["task_id"] for i in state2["pending"]])
    check("retry_invalid 标记 retry 集", state2["retry"] == {"c"})

    # ---- 证据回滚 ----
    ev_dir = tmp / "run_ev"
    writer = EvidenceWriter("run_20260922_001", ev_dir)
    for i in range(2):
        writer.emit(task_id="t", source="dialogue", type="message", actor="user", payload={"text": f"m{i}"})
    cp = writer.checkpoint()
    check("checkpoint 偏移>0", cp["offset"] > 0 and cp["seq"] == 2)
    for i in range(3):
        writer.emit(task_id="t", source="dialogue", type="message", actor="user", payload={"text": f"x{i}"})
    check("回滚前 5 行", len((ev_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()) == 5)
    writer.rollback(cp)
    lines = [json.loads(x) for x in (ev_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    check("回滚后 2 行", len(lines) == 2)
    check("回滚后 seq 复位", writer.seq == 2 and writer.peek_next()[1] == 2)
    line = writer.emit(task_id="t", source="dialogue", type="message", actor="user", payload={"text": "after"})
    check("回滚后 id 连续", line["id"] == "ev_r001_000002" and line["seq"] == 2)

    # ---- 证据手术（retry-invalid：删任务行 + 重编号 + 引用同步） ----
    ev2_dir = tmp / "run_ev2"
    w2 = EvidenceWriter("run_20260922_002", ev2_dir)
    w2.emit(
        task_id="keep1",
        source="dialogue",
        type="message",
        actor="user",
        payload={"text": "a"},
        postprocess=lambda row: row["payload"].update({"ref": row["id"]}),
    )
    w2.emit(task_id="drop", source="dialogue", type="message", actor="user", payload={"text": "b"})
    w2.emit(
        task_id="keep2",
        source="dialogue",
        type="message",
        actor="user",
        payload={"text": "c"},
        postprocess=lambda row: row["payload"].update({"ref": row["id"]}),
    )
    removed = w2.remove_tasks({"drop"})
    kept_lines = [json.loads(x) for x in (ev2_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    check("remove_tasks 删除数", removed == 1)
    check("remove_tasks 行数", len(kept_lines) == 2)
    check(
        "remove_tasks 重编号",
        [row["seq"] for row in kept_lines] == [0, 1] and kept_lines[1]["id"] == "ev_r002_000001",
    )
    check("remove_tasks 引用同步", kept_lines[1]["payload"]["ref"] == "ev_r002_000001")
    check("remove_tasks seq 复位", w2.seq == 2)
    # raw 兜底文件必须随重编号同步（2026-10-11：strict scoped-v2 门实测 raw/payload 不一致）
    raw_rows = [
        json.loads((ev2_dir / row["raw_ref"]["path"]).read_text(encoding="utf-8").strip())
        for row in kept_lines
    ]
    check("remove_tasks raw 同步", all(r == row["payload"] for r, row in zip(raw_rows, kept_lines)))

    # ---- 运行锁 ----
    lock_dir = tmp / "run_lock"
    lock_dir.mkdir(parents=True)
    lock1 = RunLock(lock_dir)
    lock1.acquire("run_lock")
    lock2 = RunLock(lock_dir)
    blocked = False
    try:
        lock2.acquire("run_lock")
    except RuntimeError:
        blocked = True
    check("活锁拒绝重入", blocked)
    lock1.release()
    stale = json.loads((lock_dir / ".lock").read_text(encoding="utf-8")) if (lock_dir / ".lock").exists() else None
    check("锁释放", stale is None)
    (lock_dir / ".lock").write_text(
        json.dumps({"pid": 999999, "host": __import__("socket").gethostname()}), encoding="utf-8"
    )
    lock3 = RunLock(lock_dir)
    ok = True
    try:
        lock3.acquire("run_lock")
    except RuntimeError:
        ok = False
    check("陈旧锁可接管", ok)
    lock3.release()

    # ---- 熔断 / usage / 停止位 ----
    breaker = CircuitBreaker(3)
    check("熔断：skip 不计", breaker.record("skip") is False)
    check("熔断：3 连 invalid 触发", [breaker.record("env_error") for _ in range(3)][-1] is True)
    breaker2 = CircuitBreaker(2)
    breaker2.record("env_error")
    check("熔断：ok 重置", breaker2.record("ok") is False and breaker2.consecutive_invalid == 0)
    check("熔断：0=关", CircuitBreaker(0).record("env_error") is False)
    check("usage total 优先", usage_tokens({"total_tokens": 9, "input_tokens": 5}) == 9)
    check("usage 求和", usage_tokens({"input_tokens": 5, "output_tokens": 2}) == 7)
    check("usage 空", usage_tokens(None) == 0)
    stop = StopFlag()
    stop.request("signal:SIGTERM")
    check("停止位", stop.requested and stop.reason == "signal:SIGTERM")

    # ---- 分片归并 ----
    shard1 = tmp / "run_x.shard1of2"
    shard2 = tmp / "run_x.shard2of2"
    for shard, task, status in ((shard1, "a", "ok"), (shard2, "b", "env_error")):
        shard.mkdir(parents=True)
        (shard / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "run_x",
                    "suite_version": "v0.2",
                    "finished_at": "2026-09-22T10:00:00+08:00",
                    "stopped_reason": None,
                    "tasks": [{"task_id": task, "status": status, "duration_ms": 1, "counts": {}}],
                    "agents": [{"name": "mock"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 两分片故意用相同 id（同 run short + seq），验证归并重编号
        lines = [
            {"id": "ev_rx_000000", "seq": 0, "run_id": "run_x", "task_id": task, "payload": {"text": task}},
            {"id": "ev_rx_000001", "seq": 1, "run_id": "run_x", "task_id": task, "payload": {"ref": "ev_rx_000000"}},
        ]
        (shard / "evidence.jsonl").write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8"
        )
        (shard / "tasks.jsonl").write_text(
            json.dumps({"event": "finish", "task_id": task, "status": status}) + "\n", encoding="utf-8"
        )
        (shard / "usage.json").write_text(json.dumps({"tokens_total": 10, "per_task": {task: {}}}), encoding="utf-8")
        (shard / "raw" / task).mkdir(parents=True)
        (shard / "raw" / task / "stdout.txt").write_text("x", encoding="utf-8")
        (shard / "raw" / "run").mkdir(parents=True)
        (shard / "raw" / "run" / "preflight.jsonl").write_text("{}\n", encoding="utf-8")
    out_dir = tmp / "run_x"
    result = merge([shard1, shard2], out_dir)
    merged = [json.loads(x) for x in (out_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    check("归并行数", len(merged) == 4 and result["evidence_lines"] == 4)
    check("归并 seq 全局递增", [line["seq"] for line in merged] == [0, 1, 2, 3])
    check("归并 id 唯一", len({line["id"] for line in merged}) == 4)
    ref = merged[3]["payload"]["ref"]
    check("归并替换行内旧 id 引用", ref == "ev_rx_000002")
    check("归并 raw 复制", (out_dir / "raw" / "a" / "stdout.txt").is_file() and (out_dir / "raw" / "b" / "stdout.txt").is_file())
    check("归并 run 伪任务 raw 合并", (out_dir / "raw" / "run" / "preflight.jsonl").is_file())
    check("归并 verdicts 占位", (out_dir / "verdicts.jsonl").exists() and (out_dir / "verdicts.jsonl").stat().st_size == 0)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    check("归并任务并集", {t["task_id"] for t in manifest["tasks"]} == {"a", "b"})
    check("归并来源登记", len(manifest["merged_from"]) == 2)
    check("归并 token 合计", result["tokens_total"] == 20)
    dup = False
    try:
        merge([shard1, shard1], tmp / "run_dup")
    except RuntimeError:
        dup = True
    check("重复任务拒绝归并", dup)

    # ---- ISS-10 回归：--force 先删后验（校验必须先于 rmtree；分片不许被输出目录吞掉） ----
    before_ev = (shard1 / "evidence.jsonl").read_text(encoding="utf-8")
    same_rejected = False
    try:
        merge([shard1], shard1, force=True)  # out == 分片
    except RuntimeError:
        same_rejected = True
    check("ISS-10 out==分片被拒", same_rejected)
    check(
        "ISS-10 分片未被吞",
        (shard1 / "evidence.jsonl").is_file()
        and (shard1 / "evidence.jsonl").read_text(encoding="utf-8") == before_ev,
    )

    outer = tmp / "outer_iss10"
    inner = outer / "run_x.shard1of1"
    shutil.copytree(shard1, inner)
    parent_rejected = False
    try:
        merge([inner], outer, force=True)  # out ⊃ 分片（rmtree 会连带吞掉分片）
    except RuntimeError:
        parent_rejected = True
    check("ISS-10 out⊃分片被拒", parent_rejected)
    check(
        "ISS-10 嵌套分片未被吞",
        (inner / "manifest.json").is_file() and (inner / "evidence.jsonl").is_file(),
    )
    check("ISS-10 合法 --force 仍可用", merge([shard1, shard2], out_dir, force=True)["evidence_lines"] == 4)

    # ---- run_batch 守卫路径（停止请求 / 预检失败） ----
    sample = {"id": "s1", "script": [{"session": "s1", "turns": [{"text": "hi"}]}]}
    stop_flag = StopFlag()
    stop_flag.request("signal:SIGTEST")
    stop_dir, stop_tasks = run_batch(
        adapters=[FakeAdapter()],
        samples=[sample],
        suite_version="v0.1",
        run_id="stop_test",
        runs_dir=tmp,
        stop=stop_flag,
    )
    stop_manifest = json.loads((stop_dir / "manifest.json").read_text(encoding="utf-8"))
    check("停止请求：不开任务", stop_tasks == [])
    check("停止请求：manifest 记 stopped_reason", stop_manifest.get("stopped_reason") == "signal:SIGTEST")
    check("停止请求：run.log 落盘", (stop_dir / "run.log").exists())
    check("停止请求：锁已释放", not (stop_dir / ".lock").exists())

    fail_dir, fail_tasks = run_batch(
        adapters=[FakeAdapter(healthy=False)],
        samples=[sample],
        suite_version="v0.1",
        run_id="preflight_fail_test",
        runs_dir=tmp,
    )
    fail_manifest = json.loads((fail_dir / "manifest.json").read_text(encoding="utf-8"))
    check("预检失败：任务记 env_error", len(fail_tasks) == 1 and fail_tasks[0]["status"] == "env_error")
    check("预检失败：stopped_reason", fail_manifest.get("stopped_reason") == "preflight_failed")
    check("预检失败：preflight.json", (fail_dir / "preflight.json").is_file())

    # ---- ISS-11 回归：同名适配器拒绝（task_id 混流 + raw 覆写，rc=0 静默损坏） ----
    dup_guard = False
    try:
        run_batch(
            adapters=[FakeAdapter(), FakeAdapter()],
            samples=[sample],
            suite_version="v0.1",
            run_id="dup_agent_test",
            runs_dir=tmp,
        )
    except RuntimeError:
        dup_guard = True
    check("ISS-11 同名适配器拒绝", dup_guard)
    check("ISS-11 拒绝时不建 run 目录", not (tmp / "dup_agent_test").exists())

    if failures:
        print(f"❌ resilience 测试未通过：{failures}")
        return 1
    print("✅ resilience 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
