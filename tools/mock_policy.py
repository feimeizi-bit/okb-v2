"""mock 行为策略文件（CONTRACT §14 扩展位的实现）：加载 / lint / 记录解析。

设计裁决（notes/c2-protocol-2026-09-21.md）：
- 一份文件 = 一档考生；消费者有两个：B 的 tools/simulate.py（离线）与 A 的
  engine/adapters/mock.py（stdio），字段名与 §14 协议逐字同名，零翻译。
- 记录解析 = 特异性排序：精确 sample > "*"；同级内 精确 session > "*"；再 精确 turn > "*"；
  平手取文件序首条。胜出记录**唯一**，op 不跨记录合并（合并交给 YAML `<<`，loader 支持）。
- lint：策略文件禁止出现样本答案字段（sample 键下不得叫 anchors/expect —— R1 的输入白名单
  是**创作纪律**，机器只拦字面违规；真正防线是 C-2 消融与 T5 应试档）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if os.path.join(ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "tools"))

import yaml  # noqa: E402

TIERS = {"remember", "coexist", "replace", "forget", "gamed"}
OPS_KEYS = {"memory_ops": {"op": {"set", "append", "delete"}, "required": {"op", "key", "value"}},
            "file_ops": {"op": {"write", "append", "delete"}, "required": {"op", "path", "content"}}}


class PolicyError(Exception):
    pass


class UnknownOpError(PolicyError):
    """非法 op / 缺必要字段 → 评分/env_error 路（§14：不得静默丢弃、不得记成 Agent 行为）。"""


@dataclass
class TurnRecord:
    reply_text: str = ""
    memory_ops: list = field(default_factory=list)
    file_ops: list = field(default_factory=list)


@dataclass
class Policy:
    path: str
    tier: str
    suite_version: str
    on_missing_record: str
    defaults_reply: str
    samples: dict          # sample_id -> list[record dict]
    raw: dict

    @classmethod
    def load(cls, path) -> "Policy":
        with open(path, encoding="utf-8") as fh:      # 不关句柄会冒 ResourceWarning，污染按输出判定的脚本
            text = fh.read()
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict):
            raise PolicyError("策略文件顶层必须是 mapping")
        for bad in ("anchors", "expect"):
            if bad in doc or (isinstance(doc.get("samples"), list) and any(bad in (s or {}) for s in doc["samples"])):
                raise PolicyError(f"策略文件禁止携带样本答案字段 {bad!r}（R1 输入白名单）")
        tier = doc.get("tier")
        if tier not in TIERS:
            raise PolicyError(f"tier {tier!r} 不在 {sorted(TIERS)}")
        samples: dict[str, list] = {}
        for item in doc.get("samples", []):
            sid = str(item.get("sample_id", "*"))
            recs = []
            for rec in item.get("turns", []):
                m = rec.get("match", {})
                for key in ("memory_ops", "file_ops"):
                    spec = OPS_KEYS[key]
                    for op in rec.get(key, []) or []:
                        if op.get("op") not in spec["op"]:
                            raise UnknownOpError(f"{sid} {key}: 非法 op {op.get('op')!r}")
                        missing = spec["required"] - set(op)
                        if missing and not (op["op"] == "delete" and "content" in missing and key == "file_ops"):
                            raise UnknownOpError(f"{sid} {key}: op 缺字段 {sorted(missing)}")
                recs.append(rec)
            samples.setdefault(sid, [])
            samples[sid] += recs
        defaults = doc.get("defaults", {}) or {}
        return cls(path=str(path), tier=tier, suite_version=str(doc.get("suite_version", "")),
                   on_missing_record=doc.get("on_missing_record", "fail"),
                   defaults_reply=str(defaults.get("reply_text", "")),
                   samples=samples, raw=doc)


def resolve(policy: Policy, sample_id: str, session: str, turn: int):
    """特异性排序取唯一胜出记录。返回 (TurnRecord, 命中的原始记录|None)；None = 纯靠 defaults。
    样本级零覆盖由 coverage_guard 在 run 前拦截（P1-2 静默通道）；单轮 defaults 兑底合法。
    第二元素供调用方做 unused-records 反向审计（id(原始 rec) 可集）。"""
    cands = []
    for sid in (sample_id, "*"):
        for rec in policy.samples.get(sid, []):
            m = rec.get("match", {})
            if str(m.get("session", "*")) not in ("*", str(session)):
                continue
            mt = m.get("turn", "*")
            if str(mt) not in ("*", str(turn)):
                continue
            score = (2 if sid == sample_id else 0) + (1 if str(mt) == str(turn) else 0)
            cands.append((score, rec))
    if not cands:
        return TurnRecord(reply_text=policy.defaults_reply), None
    best = max(cands, key=lambda c: c[0])[1]
    return TurnRecord(reply_text=str(best.get("reply_text", policy.defaults_reply)),
                      memory_ops=best.get("memory_ops") or [],
                      file_ops=best.get("file_ops") or []), best


def coverage_guard(policy: Policy, sample_ids: list[str]) -> list[str]:
    """on_missing_record=fail 的硬前提：每份被考样本必须有至少一条具名记录。
    否则「策略整体没写中」会伪装成「考生行为如此」（验收 P1-2 的漏洞）。返回无覆盖样本列表。"""
    if policy.on_missing_record != "fail":
        return []
    return [sid for sid in sample_ids if sid not in policy.samples and "*" not in policy.samples]
