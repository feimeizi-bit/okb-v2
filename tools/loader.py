"""okb 数据契约 · 读取与归一化

职责：把 YAML / JSONL 读成统一的 Python dict 列表，并抹平格式差异
（典型坑：YAML 会把 `created: 2026-09-21` 解析成 datetime.date，
 直接 json.dumps 会抛 TypeError）。
"""
from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any

import yaml


class _StrictLoader(yaml.SafeLoader):
    """拒绝重复键的 YAML 加载器。

    默认行为：`safe_load` 遇到同一个 mapping 里写两遍 `samples:` / `anchors:` 时
    静默只保留最后一个——样本会无声无息地消失（评审实测）。
    注意：必须在扁平化 `<<` 合并键【之前】检查，否则 base 与显式覆盖会被误报为重复。
    """


def _no_dup_keys(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    seen = []
    for key_node, _ in node.value:
        if isinstance(key_node, yaml.nodes.ScalarNode) and key_node.value == "<<":
            continue  # 合并键允许出现，且不参与去重
        seen.append(loader.construct_object(key_node, deep=True))
    dups = {k for k in seen if seen.count(k) > 1}
    if dups:
        raise yaml.constructor.ConstructorError(
            None, None, f"YAML 中出现重复的键：{sorted(map(str, dups))}（会静默丢掉前一份内容）", node.start_mark)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_keys)

SUITE_KEYS = {"suite_version", "samples"}
SAMPLE_LIST_KEYS = {"samples"}


def _normalize(value: Any) -> Any:
    """递归把 date/datetime 转成 ISO 字符串，保证可 JSON 序列化。"""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def load_yaml(path: str | pathlib.Path) -> tuple[str | None, list[dict]]:
    """读一个 suite 文件：{suite_version, samples: [...]}。"""
    raw = yaml.load(pathlib.Path(path).read_text(encoding="utf-8"), Loader=_StrictLoader)
    if raw is None:
        raise ValueError(f"{path}: 文件为空")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 顶层必须是映射（suite_version + samples）")
    suite_version = raw.get("suite_version")
    samples = raw.get("samples")
    if samples is None:
        # 也允许直接写一个样本列表
        raise ValueError(f"{path}: 缺少 samples 列表")
    if not isinstance(samples, list):
        raise ValueError(f"{path}: samples 必须是列表")
    return suite_version, [_normalize(s) for s in samples]


def load_jsonl(path: str | pathlib.Path) -> list[dict]:
    """读 paper.jsonl：每行一个 JSON 对象。"""
    out: list[dict] = []
    for lineno, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: JSON 解析失败: {exc}") from exc
    return out


def load_any(path: str | pathlib.Path) -> tuple[str | None, list[dict]]:
    """按扩展名分派。"""
    p = pathlib.Path(path)
    if p.suffix in (".yaml", ".yml"):
        return load_yaml(p)
    if p.suffix == ".jsonl":
        return None, load_jsonl(p)
    raise ValueError(f"{path}: 只支持 .yaml / .yml / .jsonl")


def derive_tags(sample: dict) -> list[str]:
    """tags 由字段派生，不靠作者手写（手写会漂移）。

    消费者：报告（交叉统计）。
    """
    tags: list[str] = []
    script = sample.get("script") or []
    if len(script) > 1:
        tags.append("cross_session")
    if sum(len(s.get("turns") or []) for s in script) > 1:
        tags.append("multi_turn")
    if sample.get("pii"):
        tags.append("has_pii")
    if sample.get("fixture"):
        tags.append("has_fixture")
    if sample.get("expect", {}).get("gold_evidence"):
        tags.append("has_gold_evidence")
    if any(a.get("type") == "tool_call" for a in sample.get("anchors") or []):
        tags.append("has_tool_assertion")
    if sample.get("time_anchor"):
        tags.append("has_time_anchor")
    return tags
