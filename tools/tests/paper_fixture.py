#!/usr/bin/env python3
"""pilot 题卷是可再生物，不是"必须先跑过 make-paper 的机器状态"。

为什么有这个模块：A 侧 2026-10-08 真机回执里 `test_judge_units` / `test_judge_drift` /
`test_score_units`（以及依赖它的 `test_scorer_mutation`）全红，根因同一个——它们直接读
`build/paper-v0.2.jsonl`。`build/` 被 .gitignore 排除、不进 .deb、也不在
`deploy/sync-to-vm.ps1` 的同步清单里，所以目标机上根本没有这个文件。

两条原则都在这里落地：
  ① 不能"缺输入就 SKIP"了事——我原来那句 `不得静默 SKIP` 是对的，评分逻辑确实必须真跑；
  ② 但也不能把"我的机器跑过 make-paper"当成全宇宙的前提——那是环境问题，不是产品缺陷。
解法：当场从卡片重建（`tools/build_paper.py`，亚秒级），然后**校验重建结果等于冻结 sha**。
重建不等于冻结值 = 题卷漂移，直接失败；这才叫可复现。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PILOT_YAML = "samples/pilot-v0.2.yaml"
PILOT_JSONL = "build/paper-v0.2.jsonl"
# 期望指纹**不写字面量**：从已入库的评分计划里读 `paper_sha256`（plan 与卷的绑定字段，
# 本来就是权威来源）。上一版我手抄这个 sha 多打了一个字符，四条套件一起红——
# 手抄的东西错一个字符没人看得见，让机器去读就没有这个错法。
PILOT_PLAN = "samples/four-layer-pilot.v1.json"


def frozen_sha256(root: pathlib.Path = None) -> str:
    root = pathlib.Path(root) if root else ROOT
    f = root / PILOT_PLAN
    if not f.is_file():
        raise AssertionError("缺少 %s：无法确定 pilot 卷的冻结指纹（这本身是缺陷，不静默放行）" % PILOT_PLAN)
    want = json.loads(f.read_text(encoding="utf-8")).get("paper_sha256")
    if not isinstance(want, str) or len(want) != 64:
        raise AssertionError("%s 的 paper_sha256 不是 64 位十六进制：%r" % (PILOT_PLAN, want))
    return want


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_pilot_jsonl(root: pathlib.Path = None, *, expect_sha: str = None) -> str:
    """返回可用的 pilot 卷路径；缺了就重建并核对指纹。绝不静默跳过。"""
    root = pathlib.Path(root) if root else ROOT
    out = root / PILOT_JSONL
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run([sys.executable, str(root / "tools/build_paper.py"),
                            str(root / PILOT_YAML), "-o", str(out)],
                           cwd=str(root), capture_output=True,
                           encoding="utf-8", errors="replace", timeout=300)
        if p.returncode != 0 or not out.is_file():
            raise AssertionError(
                "重建 %s 失败（rc=%s）——这确实是缺陷，不能跳过：\n%s\n%s"
                % (PILOT_JSONL, p.returncode, (p.stdout or "")[-600:], (p.stderr or "")[-600:]))
    expect_sha = expect_sha or frozen_sha256(root)
    if expect_sha:
        got = sha256(out)
        if got != expect_sha:
            raise AssertionError(
                "pilot 卷指纹漂移：%s 实际=%s 期望=%s；冻结卷被改了，评分结论全部作废"
                % (PILOT_JSONL, got, expect_sha))
    return str(out)
