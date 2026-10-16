"""判官占位接口 + §9 稳定投影缓存键（判官本体属 B 待办 #7，本文件先把键与形状钉死）。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from .predicates import canonical_json

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "schemas", "evidence.schema.json")
_TYPE_FIELDS: dict[str, set[str]] = {}


def type_payload_fields() -> dict[str, set[str]]:
    """从 evidence.schema.json 程序化取「type → payload 字段集」——避免第二真相源。"""
    global _TYPE_FIELDS
    if _TYPE_FIELDS:
        return _TYPE_FIELDS
    doc = json.load(open(_SCHEMA_PATH, encoding="utf-8"))

    def walk(o):
        if isinstance(o, dict):
            t = (o.get("properties") or {}).get("type") or {}
            const = t.get("const")
            if const and "payload" in (o.get("properties") or {}):
                pp = o["properties"]["payload"].get("properties") or {}
                _TYPE_FIELDS.setdefault(const, set()).update(pp.keys())
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return _TYPE_FIELDS


@dataclass
class JudgeDecision:
    verdict: str
    version: str
    model: str | None
    cache_key: str
    notes: str = ""


@dataclass
class CacheKeyParts:
    task_id: str
    evidence_ids: list[str]
    payload_hash: str
    contract_version: str
    judge_version: str
    template_version: str


def canonical_payload_hash(rows_by_id: dict[str, dict]) -> str:
    """投影到该 contract_version 已冻结字段集（按 type 的 schema 字段表）→ canonical JSON → sha256。

    新增字段不参与旧投影 = §9 稳定投影（T20 的断言对象）。
    """
    tf = type_payload_fields()
    proj = []
    for rid in sorted(rows_by_id):
        r = rows_by_id[rid]
        fields = tf.get(r.get("type"), set())
        payload = r.get("payload") or {}
        proj.append({"id": rid, "type": r.get("type"),
                     "payload": {k: payload.get(k) for k in sorted(fields) if k in payload}})
    return hashlib.sha256(canonical_json(proj).encode("utf-8")).hexdigest()


def build_cache_key(task_id: str, evidence_ids: list[str], rows_by_id: dict[str, dict],
                    contract_version: str, judge_version: str, template_version: str) -> str:
    parts = canonical_json([task_id, sorted(evidence_ids), canonical_payload_hash(rows_by_id),
                            contract_version, judge_version, template_version])
    return "sha256:" + hashlib.sha256(parts.encode("utf-8")).hexdigest()
