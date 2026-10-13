"""EvidenceIndex：evidence.jsonl + manifest.json + raw/* 的只读索引（§13 #9 可复现输入）。

事实依据（oracle 设计 §0 实测）：
- 快照条目**不在 payload**（evidence.schema memory_read payload 只有 phase/coverage/entries_count*），
  真身在 raw_ref.path 指向的 JSONL（runner.py:197，MemoryEntry.as_dict，已占位符化）。
  *entries_count 是否允许以 schema 为准，索引不依赖它存在。
- 坏行不静默跳过（InputError）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class EvidenceIndex:
    def __init__(self, run_dir: Path, manifest: dict, rows: list[dict]) -> None:
        self.run_dir = Path(run_dir)
        self.manifest = manifest
        self.rows = rows
        self.by_id = {r["id"]: r for r in rows}
        self._raw_cache: dict[str, list] = {}
        self.mem_probe_unverified = self._compute_mem_probe_unverified()

    # ---------- 记忆探针正向控制（G1）----------
    def agent_of(self, task_id: str) -> str:
        """task_id = {agent}-{sample}[-rN]；用 manifest 名单最长前缀切（样本名可含 '-'）。"""
        names = [a.get("name", "") for a in self.manifest.get("agents", [])]
        best = ""
        for n in names:
            if n and (task_id == n or task_id.startswith(n + "-")) and len(n) > len(best):
                best = n
        return best

    def _compute_mem_probe_unverified(self) -> set:
        """对 agent_kind==real 的 Agent：若整个 run 内它的任一 memory_read(env) 快照
        都没出现**非空条目**，则其记忆采集未证实（探针可能压根没扫到记忆面，如
        hermes 声明 memories/MEMORY.md 却 64 次没扫到）→ 该 Agent 全部 memory_entry
        锚点降 SKIP，不得把「读不到」折算成「没记住」。mock/无 kind 一律豁免
        （C-2/负向/单测夹具是受控发射器，且 U1「扫到 MEMORY.md 且空=真失忆」须保住）。"""
        names = {a.get("name", ""): str(a.get("agent_kind", ""))
                 for a in self.manifest.get("agents", [])}
        real = {n for n, k in names.items() if k == "real"}
        if not real:
            return set()
        verified: set = set()
        for r in self.rows:
            if r.get("type") != "memory_read" or r.get("actor") != "env":
                continue
            tid = r.get("task_id") or ""
            ag = self.agent_of(tid)
            if ag not in real or ag in verified:
                continue
            p = r.get("payload") or {}
            cov = p.get("coverage") or {}
            # 只信 payload 计数（不读 raw：既避免缺文件崩 load，也让「采集是否成功」
            # 完全由引擎落盘的探测元数据决定）
            if (p.get("entries_count") or 0) > 0 or (cov.get("entries_scanned") or 0) > 0:
                verified.add(ag)
        return real - verified

    # ---------- 加载 ----------
    @classmethod
    def load(cls, run_dir: Path, *, validate_schema: bool = False) -> "EvidenceIndex":
        run_dir = Path(run_dir)
        ev = run_dir / "evidence.jsonl"
        if not ev.is_file():
            raise InputErrorLocal(f"缺 evidence.jsonl: {ev}")
        mf = run_dir / "manifest.json"
        if not mf.is_file():
            raise InputErrorLocal(f"缺 manifest.json: {mf}")
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        for key in ("run_id", "agents"):
            if key not in manifest:
                raise InputErrorLocal(f"manifest 缺字段 {key!r}")
        rows = []
        for ln, line in enumerate(ev.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise InputErrorLocal(f"evidence.jsonl:{ln} 坏行: {exc}") from exc
        idx = cls(run_dir, manifest, rows)
        if validate_schema:
            idx.validate_against_schema()
        return idx

    def validate_against_schema(self) -> None:
        import json
        from jsonschema import Draft202012Validator
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根（不依赖 run_dir 位置）
        schema = json.load(open(os.path.join(root, "schemas", "evidence.schema.json"), encoding="utf-8"))
        v = Draft202012Validator(schema)
        for r in self.rows:
            errs = list(v.iter_errors(r))
            if errs:
                raise InputErrorLocal(f"证据行 {r.get('id')} 不合 schema: {errs[0].message}")

    # ---------- 查询 ----------
    def task_rows(self, task_id: str) -> list[dict]:
        return [r for r in self.rows if r.get("task_id") == task_id]

    def _by(self, task_id: str, pred) -> list[dict]:
        return [r for r in self.task_rows(task_id) if pred(r)]

    def system_action(self, task_id: str, action: str) -> dict | None:
        rows = self._by(task_id, lambda r: r["type"] == "message" and r["source"] == "system"
                        and r["payload"].get("action") == action)
        return rows[-1] if rows else None

    def snapshot(self, task_id: str, phase: str) -> dict | None:
        rows = self._by(task_id, lambda r: r["type"] == "memory_read" and r["actor"] == "env"
                        and r["payload"].get("phase") == phase)
        return rows[-1] if rows else None

    def snapshot_entries(self, line: dict) -> list[dict]:
        raw_ref = line.get("raw_ref") or {}
        path = raw_ref.get("path")
        if not path:
            raise InputErrorLocal(f"快照行 {line.get('id')} 缺 raw_ref.path")
        return self._read_raw_jsonl(path)

    def _read_raw_jsonl(self, rel: str) -> list[dict]:
        cached = self._raw_cache.get(rel)
        if cached is not None:
            return cached
        target = self.run_dir / rel
        if not target.is_file():
            raise InputErrorLocal(f"raw 文件缺失: {rel}")
        out = []
        for ln, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputErrorLocal(f"raw {rel}:{ln} 坏行: {exc}") from exc
            if isinstance(rec, dict):
                out.append(rec)
        self._raw_cache[rel] = out
        return out

    def events(self, task_id: str, *, etype: str, actor: str | None = None,
               via: tuple[str, ...] | None = None) -> list[dict]:
        def pred(r):
            if r["type"] != etype:
                return False
            if actor is not None and r["actor"] != actor:
                return False
            if via is not None and r["payload"].get("via") not in via:
                return False
            return True
        return self._by(task_id, pred)

    def agent_messages(self, task_id: str, session: str | None = None,
                       turn: int | None = None) -> list[dict]:
        def pred(r):
            if not (r["source"] == "dialogue" and r["type"] == "message" and r["actor"] == "agent"):
                return False
            if session is not None and r.get("session_id") != session:
                return False
            if turn is not None and r.get("turn") != turn:
                return False
            return True
        return self._by(task_id, pred)

    def user_messages(self, task_id: str) -> list[dict]:
        return self._by(task_id, lambda r: r["source"] == "dialogue" and r["type"] == "message"
                        and r["actor"] == "user")

    def file_rows(self, task_id: str, path: str) -> list[dict]:
        return self._by(task_id, lambda r: r["type"] == "file_write" and r["payload"].get("path") == path)

    def listing(self, task_id: str) -> tuple[list[dict], str] | None:
        row = self.system_action(task_id, "workspace_listing")
        if row is None:
            return None
        entries = self._read_raw_jsonl(row["raw_ref"]["path"])
        return entries, row["id"]

    def total_turns(self, sample: dict) -> int:
        return sum(len(s.get("turns", [])) for s in sample.get("script", []))


class InputErrorLocal(Exception):
    """run 输入不可用（CLI 映射 exit 2 / task env_error）。"""
