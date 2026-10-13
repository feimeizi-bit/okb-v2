"""谓词工具：正则缓存、记忆条目命中、产物 field_path 取值、canonical JSON。

契约依据与裁决记录（notes/c2-protocol-2026-09-21.md §裁决表）：
- 锚点求值**不做** normalize_v1（疑点 Q1 裁决 a）：正则只在占位符空间的存盘文本上逐字跑。
- 正则 flags：无（疑点 Q6 裁决 a）：区分大小写、`.` 不跨行。
- 记忆 key 比较：仅 trim、大小写敏感（疑点 Q5 裁决 a）；field_path 键名宽松规则
  由 engine.paths 自带（trim + 大小写不敏感），两者不混用。
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Iterable, Optional

import yaml  # noqa: F401  (产物解析用)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from engine import paths as okb_paths  # noqa: E402  (§15 #8：与引擎同一实现)

_RX_CACHE: dict[str, "re.Pattern[str]"] = {}


def compile_rx(pattern: str) -> "re.Pattern[str]":
    key = str(pattern)
    rx = _RX_CACHE.get(key)
    if rx is None:
        try:
            rx = re.compile(key)
        except re.error as exc:  # E8（loader E110 已前挡；防御性保留）
            from .model import EnvError
            raise EnvError(f"谓词正则不可编译 {pattern!r}: {exc}") from exc
        _RX_CACHE[key] = rx
    return rx


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rel_key(k) -> str:
    return str(k).strip()


def scalarize(value) -> str:
    """field_path 取到的值 → 文本（供 regex 匹配）。T9 语义。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) and value.is_integer() else str(value)
    return canonical_json(value)


def hit_entries(entries: Iterable[dict], anchor: dict) -> list[dict]:
    """memory_entry 锚点在快照条目上的命中集。

    match_scope（写死）：value→仅 value；key→仅 key；both(默认)→rx.search(key) or rx.search(value)。
    key 等值过滤与 key_regex 可叠加；key=="*"= 全量（E116 保证带 value_regex）。
    """
    rx = None
    if anchor.get("value_regex") is not None:
        rx = compile_rx(str(anchor["value_regex"]))
    elif anchor.get("regex") is not None:
        rx = compile_rx(str(anchor["regex"]))
    key_eq = anchor.get("key")
    key_rx = compile_rx(str(anchor["key_regex"])) if anchor.get("key_regex") else None
    scope = anchor.get("match_scope") or "both"
    out = []
    for e in entries:
        k, v = rel_key(e.get("key", "")), str(e.get("value", ""))
        if key_eq not in (None, "*", ""):
            if k != rel_key(key_eq):
                continue
        if key_rx is not None and not (key_rx.search(k) or (scope == "both" and key_rx.search(v))):
            continue
        if rx is not None:
            if scope == "value":
                ok = bool(rx.search(v))
            elif scope == "key":
                ok = bool(rx.search(k))
            else:  # both：逐字段分离，不拼接
                ok = bool(rx.search(k) or rx.search(v))
            if not ok:
                continue
        out.append(e)
    return out


def field_value(content_text: str, filename: str, field_path: str):
    """产物文件内按 field_path 定位字段值（§5 产物边界：不得退化为全文匹配）。

    解析格式：.yaml/.yml → yaml.safe_load；.json → json.loads；其他 → EnvError（Q 裁决）。
    解析失败 / 路径不存在 → EnvError（E9，附定位串）。
    """
    from .model import EnvError
    name = filename.lower()
    try:
        if name.endswith(".json"):
            doc = json.loads(content_text)
        elif name.endswith((".yaml", ".yml")):
            doc = yaml.safe_load(content_text)
        else:
            raise EnvError(f"field_path 不支持的文件格式: {filename}")
    except EnvError:
        raise
    except Exception as exc:
        raise EnvError(f"产物 {filename} 解析失败: {exc}") from exc
    import os
    try:
        return okb_paths.resolve(doc, field_path)
    except okb_paths.PathNotFound as exc:
        raise EnvError(f"field_path {field_path!r} 在 {filename} 中无法解析: {exc}") from exc
