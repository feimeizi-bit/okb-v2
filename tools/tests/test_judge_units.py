#!/usr/bin/env python3
"""判官线 v0.2 单测：few-shot 防泄题 / 预算闸 / 模板版本→缓存键 / 可见包开卷自检。
用法：python tools/tests/test_judge_units.py"""
from __future__ import annotations
from paper_fixture import ensure_pilot_jsonl  # build/ 不在 VM 同步范围：缺了就重建并核对冻结 sha
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(ROOT, "tools"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from scoring.judge_client import (OpenAICompatJudge, JudgeError, pick_fewshots,      # noqa: E402
                                  build_visible_pack, FEWSHOTS, DIM_FOCUS)           # noqa: E402
from scoring.judge import build_cache_key                                          # noqa: E402
from scoring.model import AnchorOutcome, R_PASS                                     # noqa: E402
from loader import load_jsonl                                                      # noqa: E402

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


class FakeIdx:
    def __init__(self, rows):
        self.by_id = {r["id"]: r for r in rows}
        self.manifest = {"contract_version": "v0.1"}


def main() -> int:
    paper = load_jsonl(ensure_pilot_jsonl())

    # J1: few-shot 对全部 20 样本都不同 id 不同维度
    ok = all(all(f["src_id"] != s["id"] and f["dim"] != s["dimension"]
                 for f in pick_fewshots(s["id"], s["dimension"]))
             for s in paper)
    check("J1 few-shot 选取：不同 id 且不同维度（20 样本全量）", ok)
    check("J2 few-shot 案例库带元数据", all({"src_id", "dim", "case"} <= set(f) for f in FEWSHOTS))

    # J3: 可见包开卷自检——构造会泄漏 expect.verdict 组合式的极端情况：
    #     正常样本应干净；同时自检代码本身在位（把 forbidden 片段塞进 notes 会触发拒绝）
    soft = [AnchorOutcome(anchor_id="S1", atype="memory_entry", expect="present", severity="soft",
                           result=R_PASS, hit_count=1, evidence_refs=["ev_1"])]
    idx = FakeIdx([{"id": "ev_1", "type": "memory_read", "actor": "env",
                    "payload": {"phase": "after", "entries_count": 1, "coverage": {}}}])
    s_ok = paper[0]
    _, parts = build_visible_pack(s_ok, soft, idx, "t")
    blob = json.dumps(parts, ensure_ascii=False)
    check("J3 正常样本可见包不含 verdict 组合式", '{"verdict": "remembered"}' not in blob)
    poisoned = dict(s_ok, expect=dict(s_ok.get("expect", {}), notes='{"verdict": "remembered"}'))
    try:
        build_visible_pack(poisoned, soft, idx, "t")
        check("J4 notes 夹带答案组合式被拒", False)
    except JudgeError:
        check("J4 notes 夹带答案组合式被拒（宁炸不泄）", True)

    # J5: 模板/judge 版本变 → 缓存键变（replay 对位）
    k1 = build_cache_key("t", ["ev_1"], idx.by_id, "v0.1", "okb-jr-v0.2", "okb-jr-v0.2")
    k2 = build_cache_key("t", ["ev_1"], idx.by_id, "v0.1", "okb-jr-v0.3", "okb-jr-v0.2")
    check("J5 judge_version 变→缓存键变", k1 != k2)

    # J6: 预算闸——max_calls=0 且无缓存命中 → judge_soft 抛 JudgeError（走 judge_error 路）
    jdg = OpenAICompatJudge.__new__(OpenAICompatJudge)
    jdg.max_calls, jdg.calls, jdg.cache_hits, jdg.allowed = 0, 0, 0, ["remembered"]
    jdg._cache = {}
    jdg.version, jdg.model = "t", "m"
    try:
        jdg.judge_soft(s_ok, soft, idx, "t1")
        check("J6 预算闸触发 JudgeError", False)
    except JudgeError as e:
        check("J6 预算闸触发 JudgeError（不落网、不污染分数）", "预算闸" in str(e))

    # J7: 池轮换全覆盖——12 条 few-shot 必须至少被 20 样本中的一个选中（[:k] 死条目回归）
    used = set()
    for s in paper:
        for f in pick_fewshots(s["id"], s["dimension"]):
            used.add(id(f))
    covered = sum(1 for f in FEWSHOTS if id(f) in used)
    check(f"J7 few-shot 轮换全覆盖 {covered}/{len(FEWSHOTS)}", covered == len(FEWSHOTS))
    # J8: shadow 可见包不得携带规则 result（防照抄）
    s_ok2 = dict(s_ok, expect=dict(s_ok.get("expect", {})))
    _, parts_sh = build_visible_pack(s_ok2, soft, idx, "t", shadow=True)
    clean = all(set(x.keys()) == {"id"} for x in parts_sh.get("soft_anchors_status", []))
    check("J8 shadow 状态行纯 id（无 result/expect/severity）", clean and bool(parts_sh["soft_anchors_status"]))
    ad_clean = all(set(x.keys()) == {"id"} for x in parts_sh.get("anchors_desc", []))
    check("J8b shadow anchors_desc 纯 id", ad_clean)

    # J9: rubric 文档↔代码逐条对拍（关键词断言曾掩盖分叉——二轮验收指实）
    doc = open(os.path.join(ROOT, "docs", "judge-rubric-v0.2.md"), encoding="utf-8").read()
    from scoring.judge_client import RUBRIC_COMMON
    pairs = [("仅适用记忆层", "仅适用记忆层"), ("复合形态择重", "复合形态择重"),
             ("掩码", "掩码"), ("boundary_kind", "boundary_kind"),
             ("low-confidence", "low-confidence"), ("只降置信", "只降置信")]
    check("J9 六短语双向在位（文档/RUBRIC_COMMON）", all((a in doc) and (b in RUBRIC_COMMON) for a, b in pairs))
    check("J9b DIM/Few-shot 与产物从严同向", "产物层无叙述豁免" in DIM_FOCUS["dynamic_update"]
          and "记忆层豁免" in json.dumps(FEWSHOTS, ensure_ascii=False))

    test_cross_model_cache_guard()

    print("\n" + (f"❌ 失败 {len(FAILS)}: {FAILS}" if FAILS else "✅ 判官线单测全部通过"))
    return 1 if FAILS else 0



def test_cross_model_cache_guard():
    """换判官不吃旧缓存（_hit_usable）+ 同键后写覆盖装载。"""
    import tempfile
    from scoring.judge_client import OpenAICompatJudge
    with tempfile.TemporaryDirectory() as td:
        j = OpenAICompatJudge(repo_root=td, base_url="http://invalid.local",
                              api_key="x", cache_dir=td, model="gpt-6-sol")
        old = {"cache_key": "k1", "model": "qwen3.8-max-0902", "verdict": "remembered"}
        new = {"cache_key": "k1", "model": "gpt-6-sol", "verdict": "confused"}
        check("跨模型命中被拒", j._hit_usable(old) is False)
        check("同模型命中放行", j._hit_usable(new) is True)
        check("无裁决命中被拒", j._hit_usable({"cache_key": "k1", "model": "gpt-6-sol"}) is False)
        # 后写覆盖：先旧后新两条同键 → 装载器给出新判官的行
        with open(os.path.join(td, "judge_cache.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(old) + "\n" + json.dumps(new) + "\n")
        j2 = OpenAICompatJudge(repo_root=td, base_url="http://invalid.local",
                               api_key="x", cache_dir=td, model="gpt-6-sol")
        check("装载后写覆盖：同键取最后一条", j2._cache["k1"]["model"] == "gpt-6-sol"
              and j2._hit_usable(j2._cache["k1"]) is True)


if __name__ == "__main__":
    sys.exit(main())
