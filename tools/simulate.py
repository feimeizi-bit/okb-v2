#!/usr/bin/env python3
"""okb · 离线证据模拟器（C-2 预跑夹具，非 §14 正式通路）。

边界声明（metis R7 / oracle §4-4）：
- 本模拟器 = B 侧夹具：策略 → **真实走 engine.runner.run_batch 全链路**（fixture 布置、
  快照、diff、listing、PII 先判定后替换全部复用统一采集管线），不自建发射器、不直接写
  evidence.jsonl。它的产物**只用于开发评分器与 C-2 预跑**；正式 C-2 证据须由 A 的
  engine/adapters/mock.py（stdio 子进程，ISS-05）跑同一批策略文件，两路 verdicts 一致
  才算台账「已取得」。判据演进（逐字节→v2 归一化逐字段+绝对下限+PII 探针+事件流多重集）
  登记于 notes/c2-protocol-v0.2.md 勘误 F-6；执行器 = tools/c2_parity.py。
- search_ops/tool_ops 不在 v0 实现（pilot 的事件锚点全为 soft 或注定 skip；伪造 via=event
  采集能力反而是 R12 的失效模式）。

CLI：python tools/simulate.py --paper build/paper.jsonl --policy samples/mock-policies/remember.v0.1.yaml \
      --run-id mock_c2_001 [--runs-dir runs] [--samples id1,id2]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
from pathutil import rel_display  # 跨盘符安全：os.path.relpath 在 Windows 跨盘会 ValueError
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import redact as R                      # noqa: E402
from engine.adapter import (AgentAdapter, Capabilities, MemorySnapshot, TurnResult,  # noqa: E402
                            parse_markdown_memory)
from engine.runner import run_batch                 # noqa: E402
from loader import load_jsonl                       # noqa: E402
import mock_policy                                  # noqa: E402


class PolicyAdapter(AgentAdapter):
    """合成考生：按策略文件逐轮应答，ops 真实应用到记忆面/文件面（§14 命令语义）。"""

    name = "mock"

    def __init__(self, policy: mock_policy.Policy, samples_by_id: dict, config: dict | None = None):
        super().__init__(config or {"policy_path": policy.path, "tier": policy.tier})
        self.policy = policy
        self.samples_by_id = samples_by_id
        self.entries: list[dict] = []           # 内部记忆状态 [{key,value}]
        self._pii: R.PiiPipeline | None = None
        self._root: Path | None = None
        self._fired: set[int] = set()            # 命中的策略记录（id 集），供 unused 审计
        self._wcnt = 0                           # memory_write 事件计数（event_coverage 用）

    def event_coverage(self) -> dict:
        """P2-4：覆写基类全 0——此前 manifest 把「采到了」写成「没采到」（§15#7）。"""
        return {"memory_write_via_memory_api": self._wcnt, "memory_read_via_agent": 0}

    # ---- 能力与根 ----
    def capabilities(self) -> Capabilities:
        return Capabilities(dialogue=True, memory=True, file=True, tool=True,
                            supports_time_anchor=True, notes="mock 合成考生（离线模拟器）")

    def file_root(self, ctx) -> Path:
        return Path(ctx.workspace)

    def workspace_ignore(self) -> set[str]:
        return {"MEMORY.md"}

    # ---- 生命周期 ----
    def reset(self, ctx):
        self._root = self.file_root(ctx)
        self.entries = []
        self._pii = None
        (self._root / "MEMORY.md").write_text("", encoding="utf-8", newline="\n")
        # 与 engine/adapters/mock.py:169-175 逐字段同形（momus 事件层取证：payload.policy
        # 此前仅 harness 有，104/104 task 不等）
        return [{"ok": True, "cmd": "mock-reset",
                 "policy": rel_display(self.policy.path, ROOT)}]

    def _memory_path(self) -> Path:
        return self._root / "MEMORY.md"

    def _render_memory(self):
        body = "".join(f"- {e['value']}\n" for e in self.entries)
        self._memory_path().write_text(body, encoding="utf-8", newline="\n")

    def probe_memory(self, phase: str, *, for_task_id: str | None = None) -> MemorySnapshot:
        p = self._memory_path()
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime).isoformat() if p.is_file() else None
        entries = parse_markdown_memory(text, "MEMORY.md", mtime)
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        return MemorySnapshot(phase=phase, taken_at=now, entries=entries, coverage={
            "files_scanned": 1, "entries_scanned": len(entries),
            "bytes_scanned": len(text.encode("utf-8")), "sources": ["MEMORY.md"]})

    # ---- 轮次 ----
    def _sample_id(self, task_id: str) -> str:
        sid = re.sub(r"-r\d+$", "", str(task_id))
        return sid[len("mock-"):] if sid.startswith("mock-") else sid

    def _pii_for(self, sample: dict) -> R.PiiPipeline:
        if self._pii is None:
            self._pii = R.PiiPipeline(R.load_pii(sample.get("pii", [])))
        return self._pii

    def send(self, ctx, session, turn_index, text, session_first_turn, timeout=None) -> TurnResult:
        t0 = time.monotonic()
        sid = self._sample_id(ctx.task_id)
        sample = self.samples_by_id.get(sid)
        if sample is None:
            raise mock_policy.PolicyError(f"模拟器找不到样本 {sid}")
        rec, best = mock_policy.resolve(self.policy, sid, session, turn_index + 1)
        if best is not None:
            self._fired.add(id(best))   # 策略侧反向检测：声明了却没打中的记录（验收 P1-2 补全）
        # best is None = 本轮靠 defaults 兑底（defaults 是显式声明，合法）；
        # 样本级零覆盖已由 coverage_guard 在 run 前拒绝，静默通道已封。
        pii = self._pii_for(sample)
        mat = lambda s: R.materialize(str(s), pii.entries)  # noqa: E731  占位符→真实值（Agent 侧）
        events = []   # 与 engine/adapters/mock.py 逐字段同形：set/append 发写事件、
        # delete 不发（靠前后快照 diff 可见）——C-2 对拍发现的保真缺口在此弥合
        for op in rec.memory_ops:
            key, value = mat(op["key"]), mat(op.get("value", ""))
            cur = {e["key"]: i for i, e in enumerate(self.entries)}
            if op["op"] == "set":
                if key in cur:
                    self.entries[cur[key]]["value"] = value
                else:
                    self.entries.append({"key": key, "value": value})
            elif op["op"] == "append":
                self.entries.append({"key": key, "value": value})   # 并列条目 = 真实 confused 形态
            elif op["op"] == "delete":
                self.entries = [e for e in self.entries if e["key"] != key]
            if op["op"] in ("set", "append"):
                events.append({"op": op["op"], "key": key, "value": value})
        self._wcnt += len(events)
        self._render_memory()
        for op in rec.file_ops:
            rel = str(op["path"]).replace("\\", "/")
            target = R_resolve(self._root, rel)
            if op["op"] == "delete":
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                body = mat(op.get("content", ""))
                if op["op"] == "append" and target.is_file():
                    body = target.read_text(encoding="utf-8") + body
                target.write_text(body, encoding="utf-8", newline="\n")
        reply = mat(rec.reply_text)
        usage = {"record": {"session": session, "turn": turn_index + 1},
                 "memory_ops": len(rec.memory_ops), "file_ops": len(rec.file_ops)}
        return TurnResult(reply_text=reply, stdout=json.dumps(usage, ensure_ascii=False),
                          stderr="", exit_code=0, duration_ms=int((time.monotonic() - t0) * 1000),
                          session_id=None, model=None, usage=None, memory_events=events)


def R_resolve(root: Path, rel: str) -> Path:
    """路径安全（§6）：越界 → 抛错（runner 映射 env_error）。"""
    base = Path(root).resolve()
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"越界路径: {rel}")
    return target


def simulate(paper_path, policy_path, run_id: str, runs_dir="runs", samples_filter=None):
    policy = mock_policy.Policy.load(policy_path)
    samples = load_jsonl(paper_path)
    if samples_filter:
        samples = [s for s in samples if s["id"] in set(samples_filter)]
    suite_version = (samples[0].get("meta") or {}).get("suite_version") if samples else None
    # suite 对位（验收 P2-1）+ 覆盖前提（验收 P1-2）：不合格拒整个 run，不产出误导性证据
    if policy.suite_version and suite_version and str(policy.suite_version) != str(suite_version):
        raise mock_policy.PolicyError(f"策略 suite_version={policy.suite_version} ≠ paper {suite_version}")
    uncovered = mock_policy.coverage_guard(policy, [s["id"] for s in samples])
    if uncovered:
        raise mock_policy.PolicyError(f"策略对以下样本零覆盖（on_missing_record=fail）：{uncovered}")
    ad = PolicyAdapter(policy, {s["id"]: s for s in samples})
    run_dir, tasks = run_batch(adapters=[ad], samples=samples, suite_version=suite_version,
                               run_id=run_id, runs_dir=runs_dir)
    # manifest 后处理：agent_kind=mock + 策略指纹（ISS-05 的 B 侧临时绕行，A 落地后移除）
    mf_path = Path(run_dir) / "manifest.json"
    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    if manifest.get("agents"):
        a0 = manifest["agents"][0]
        a0["agent_kind"] = "mock"
        a0["model"] = None
        a0["temperature"] = None
        # CR-3/A 备注①：event_coverage 由引擎按统一键名写入（ISS-03/04 修复后），
        # B 侧不再自造键名；此处仅保留 mock 语义覆写（读事件模拟器 v0 不发→全 0 由
        # PolicyAdapter.event_coverage() 提供）。
    import hashlib
    manifest["policy"] = {"path": rel_display(str(policy_path), ROOT),
                          "tier": policy.tier, "policy_version": policy.raw.get("policy_version"),
                          "suite_version": policy.suite_version,
                          "unused_records": [
                              {"sample_id": sid, "match": r.get("match")}
                              for sid, recs in policy.samples.items()
                              for r in recs if id(r) not in ad._fired],
                          "sha256": hashlib.sha256(Path(policy_path).read_bytes()).hexdigest()}
    mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return run_dir, tasks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="okb 离线证据模拟器（§14 v0 夹具）")
    ap.add_argument("--paper", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--samples", default=None, help="逗号分隔 sample id 过滤")
    args = ap.parse_args(argv)
    run_dir, tasks = simulate(args.paper, args.policy, args.run_id, args.runs_dir,
                              (args.samples.split(",") if args.samples else None))
    ok = sum(1 for t in tasks if t["status"] == "ok")
    print(f"✅ 模拟 run 完成：{run_dir} · tasks={len(tasks)} ok={ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
