"""okb 引擎 · 证据采集器（I-1 / I-11 / I-12）

- `EvidenceWriter.emit()`：分配 `id`/`seq`/`ts` → 先判定 PII（`pii_hits[]`）→ 后替换
  → 落 `evidence.jsonl`（一行一 JSON，追加 + flush）
- `raw_ref` 必须能定位到文件 + 行号（I-11）；调用方不传时在 postprocess 完成后
  把最终 payload 写入 `raw/<task_id>/evidence_<id>.jsonl` 兜底
- 大文件截断：头 32KB + 尾 32KB + `content_sha256`（替换后完整内容，P2-4）
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from engine import paths as pathutil
from engine.redact import PiiPipeline

HEAD_BYTES = 32 * 1024
TAIL_BYTES = 32 * 1024
_SURFACE_BY_SOURCE = {"dialogue": "reply", "memory": "memory", "file": "file", "tool": "tool"}


def now_iso() -> str:
    """带时区的 ISO 8601（毫秒）。"""
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def run_short(run_id: str) -> str:
    """run 短号：`run_20260921_001` → `r001`（用于 evidence id）。"""
    tail = re.split(r"[^0-9a-zA-Z]+", run_id)[-1] if run_id else ""
    tail = tail or "000"
    return "r" + tail[-3:]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def replace_ids(obj: Any, mapping: dict[str, str]) -> Any:
    """递归替换 JSON 结构中的旧证据 id（字符串精确匹配；pii_hits[].evidence_id 等引用同步）。"""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [replace_ids(item, mapping) for item in obj]
    if isinstance(obj, dict):
        return {key: replace_ids(value, mapping) for key, value in obj.items()}
    return obj


def sync_raw_payload(run_dir: Path, old_payload: Any, new_payload: Any, raw_ref: dict | None) -> bool:
    """重编号后同步“兜底 raw 文件”的内容（仅当 raw 恰为旧 payload 的单行 JSONL）。

    证据手术（remove_tasks）/归并（merge_runs）会把 payload 里的旧 id 引用换成新
    id，但 raw/ 下的兜底副本是写盘时的历史字节；不同步会让 strict scoped-v2 的
    raw/payload 一致性门整批误报（实测：多轮 --retry-invalid 后 201/273 openclaw
    任务被挡）。只重写「内容 == 旧 payload」的单行 JSONL；文本 raw、多行 raw、
    调用方自定义 raw 一律不动。
    """
    if not raw_ref:
        return False
    try:
        if int(raw_ref.get("line", 1) or 1) != 1:
            return False
    except (TypeError, ValueError):
        return False
    rel = str(raw_ref.get("path") or "")
    if not rel.startswith("raw/"):
        return False
    path = run_dir / rel
    if not path.is_file():
        return False
    try:
        objs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return False
    if len(objs) != 1 or objs[0] != old_payload:
        return False
    path.write_text(json.dumps(new_payload, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return True


def truncate_text(text: str, head_bytes: int = HEAD_BYTES, tail_bytes: int = TAIL_BYTES) -> tuple[str, bool, int, int]:
    """按字节做头尾截断，返回 (落盘文本, truncated, full_bytes, stored_bytes)。"""
    full_bytes = len(text.encode("utf-8"))
    if full_bytes <= head_bytes + tail_bytes:
        return text, False, full_bytes, full_bytes

    def _cut(chars: str, budget: int, from_end: bool) -> str:
        used = 0
        out: list[str] = []
        seq = reversed(chars) if from_end else chars
        for ch in seq:
            size = len(ch.encode("utf-8"))
            if used + size > budget:
                break
            used += size
            out.append(ch)
        if from_end:
            out.reverse()
        return "".join(out)

    marker = "\n…[truncated]…\n"
    stored = _cut(text, head_bytes, False) + marker + _cut(text, tail_bytes, True)
    return stored, True, full_bytes, len(stored.encode("utf-8"))


class RawStore:
    """`raw/<task_id>/` 落盘；返回的 raw_ref 为相对 runs/<run_id>/ 的路径。"""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.raw_dir = self.run_dir / "raw"

    def path_for(self, task_id: str, name: str) -> str:
        return f"raw/{task_id}/{name}"

    def write_text(self, task_id: str, name: str, text: str) -> dict:
        target = self.raw_dir / task_id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")  # F1：raw 字节跨平台确定
        return {"path": self.path_for(task_id, name), "line": 1}

    def write_bytes(self, task_id: str, name: str, data: bytes) -> dict:
        target = self.raw_dir / task_id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": self.path_for(task_id, name), "line": 1}

    def write_jsonl(self, task_id: str, name: str, records: list[Any]) -> dict:
        target = self.raw_dir / task_id / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"path": self.path_for(task_id, name), "line": 1}


class EvidenceWriter:
    def __init__(self, run_id: str, run_dir: str | Path, pii: PiiPipeline | None = None):
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_path = self.run_dir / "evidence.jsonl"
        self.raw = RawStore(self.run_dir)
        self.pii = pii
        self.short = run_short(run_id)
        self.seq = self._resume_seq()

    def _resume_seq(self) -> int:
        if not self.evidence_path.exists():
            return 0
        last = 0
        for line in self.evidence_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                last = max(last, int(json.loads(line).get("seq", -1)) + 1)
            except (json.JSONDecodeError, ValueError):
                continue
        return last

    def peek_next(self) -> tuple[str, int]:
        """预取下一个证据 id / seq（快照类证据需要先处理 PII、写 raw，再发证据行）。"""
        return f"ev_{self.short}_{self.seq:06d}", self.seq

    def checkpoint(self) -> dict:
        """记录当前证据文件尾偏移与下一个 seq（长跑保障：失败重试 / 断点续跑的回滚点）。"""
        offset = self.evidence_path.stat().st_size if self.evidence_path.exists() else 0
        return {"offset": offset, "seq": self.seq}

    def rollback(self, checkpoint: dict) -> None:
        """把证据文件截断回 checkpoint 并回退 seq。

        仅用于「任务级重试 / 续跑重开 in-flight 任务」：该任务的半截证据等价于从未发生，
        评分器只会看到最终尝试的完整证据流（raw 由调用方按任务目录整体清除后重写）。
        """
        offset = int(checkpoint.get("offset", 0))
        with self.evidence_path.open("r+b") as fh:
            fh.truncate(offset)
        self.seq = int(checkpoint.get("seq", 0))

    def remove_tasks(self, task_ids: set[str]) -> int:
        """删除指定任务的证据行并整体重编号（`--retry-invalid` 用，防旧失败证据与新证据重复）。

        证据 id 被 `pii_hits[].evidence_id` 引用，重编号时同步替换；raw 由调用方清理。
        只允许在评分前调用（B 的 verdicts 引用证据 id，评分后不得改写）。
        """
        if not self.evidence_path.exists():
            return 0
        kept: list[dict] = []
        removed = 0
        for raw in self.evidence_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if record.get("task_id") in task_ids:
                removed += 1
                continue
            kept.append(record)
        mapping: dict[str, str] = {}
        for index, record in enumerate(kept):
            old_id = str(record.get("id", ""))
            if old_id:
                mapping[old_id] = f"ev_{self.short}_{index:06d}"
        with self.evidence_path.open("w", encoding="utf-8", newline="\n") as fh:
            for index, record in enumerate(kept):
                new_record = replace_ids(record, mapping)
                new_record["id"] = mapping.get(str(record.get("id", "")), new_record.get("id"))
                new_record["seq"] = index
                sync_raw_payload(self.run_dir, record.get("payload"), new_record.get("payload"),
                                 record.get("raw_ref"))
                fh.write(json.dumps(new_record, ensure_ascii=False) + "\n")
        self.seq = len(kept)
        return removed

    def emit(
        self,
        *,
        task_id: str,
        source: str,
        type: str,
        actor: str,
        payload: dict,
        session_id: str | None = None,
        turn: int | None = None,
        raw_ref: dict | None = None,
        surface: str | None = None,
        pii: PiiPipeline | None = None,
        postprocess: "Callable[[dict], None] | None" = None,
        ev_id: str | None = None,
    ) -> dict:
        expected_id, _ = self.peek_next()
        if ev_id is None:
            ev_id = expected_id
        elif ev_id != expected_id:
            raise ValueError(f"ev_id 与下一个序号不一致：{ev_id} != {expected_id}")
        self.seq += 1
        payload = copy.deepcopy(payload)

        # 先判定、后替换（system 行由引擎构造、不含 PII，跳过扫描）
        pipeline = pii if pii is not None else self.pii
        hits: list[dict] = []
        if pipeline is not None and surface is not None and source != "system":
            for rel_path, value in list(pathutil.iter_strings(payload, "")):
                field_path = f"payload.{rel_path}" if rel_path else "payload"
                redacted, found = pipeline.process(value, field_path, surface, ev_id)
                if found:
                    pathutil.set_in(payload, rel_path, redacted)
                    hits.extend(found)

        line = {
            "id": ev_id,
            "run_id": self.run_id,
            "seq": self.seq - 1,
            "ts": now_iso(),
            "task_id": task_id,
            "session_id": session_id,
            "turn": turn,
            "source": source,
            "type": type,
            "actor": actor,
            "payload": payload,
            # Reserve the reference without writing a pre-postprocess payload.
            # Callbacks can inspect its final location; caller-owned raw is untouched.
            "raw_ref": raw_ref or {
                "path": self.raw.path_for(task_id, f"evidence_{ev_id}.jsonl"), "line": 1,
            },
            "pii_hits": hits,
        }
        if postprocess is not None:
            postprocess(line)
        if not raw_ref:
            line["raw_ref"] = self._fallback_raw_ref(task_id, ev_id, line["payload"])
        with self.evidence_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return line

    def _fallback_raw_ref(self, task_id: str, ev_id: str, payload: dict) -> dict:
        return self.raw.write_jsonl(task_id, f"evidence_{ev_id}.jsonl", [payload])

    def default_surface(self, source: str) -> str | None:
        return _SURFACE_BY_SOURCE.get(source)

    def write_raw_text(self, task_id: str, name: str, text: str) -> dict:
        return self.raw.write_text(task_id, name, text)

    def write_raw_json(self, task_id: str, name: str, obj: Any) -> dict:
        return self.raw.write_jsonl(task_id, name, [obj])
