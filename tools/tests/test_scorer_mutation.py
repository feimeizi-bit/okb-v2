#!/usr/bin/env python3
"""评分器变异测试（协议 R11 / metis 自守护 #16：对「仪器」的验收）。

原理：把 8 个已知形状的缺陷逐条注入 scoring/c2_run 的副本，跑同一套断言
（test_score_units，含分母守卫）。每个变异都必须被至少一条断言抓红；
没有抓红 = 守卫盲区 → 本套件 exit 1。

注入全部是文本级单点替换（apply 前断言唯一命中，防止"变异根本没生效"的假绿）。
用法：python tools/tests/test_scorer_mutation.py [-v]
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

MUTS = [
    ("M1 absent@空集判通过（§5 空集语义失效）",
     "tools/scoring/anchors.py",
     '        return _skipped(anchor, "empty_memory", refs=lines, detail="快照 0 条目，absent 不可判")',
     '        return _mk(anchor, result=R_PASS, hit_count=0, evidence_refs=lines, scan_scope=lines, detail="mut M1")'),
    ("M2 事件锚点无采集时判通过（§15 #7 失效）",
     "tools/scoring/anchors.py",
     '        return _skipped(anchor, "no_event_collection", detail="无 actor=agent 记忆读取事件（§15 #7）")',
     '        return _mk(anchor, result=R_PASS, hit_count=0, evidence_refs=[], detail="mut M2")'),
    ("M3 must_differ 守卫被忽略（写回原值白过）",
     "tools/scoring/anchors.py",
     '    if anchor["expect"] == "present" and anchor.get("must_differ_from_fixture"):',
     '    if False and anchor["expect"] == "present" and anchor.get("must_differ_from_fixture"):'),
    ("M4 评分期注入归一化（Q1 裁决被违：正则静默恒不命中）",
     "tools/scoring/predicates.py",
     '        k, v = rel_key(e.get("key", "")), str(e.get("value", ""))',
     '        k, v = rel_key(e.get("key", "")), re.sub(r"[-\\s]", "", str(e.get("value", "")))'),
    ("M5 fixture 掩盖末态（文件权威序倒置）",
     "tools/scoring/anchors.py",
     "    last_write = rows[-1] if rows else None",
     '    last_write = None if path in fix else (rows[-1] if rows else None)'),
    ("M6 env 快照被当 agent 事件（资格表失效）",
     "tools/scoring/anchors.py",
     '    rows = idx.events(task_id, etype="memory_read", actor="agent")  # §5 资格表：env 快照永不满足',
     '    rows = idx.events(task_id, etype="memory_read", actor=None)  # mut M6'),
    ("M7 skip 计入分母（分母漂移可造单调）",
     "tools/c2_run.py",
     '        counted = [sid for sid in core if out[tier].get(sid, {}).get("status") == "ok"]',
     '        counted = [sid for sid in core if out[tier].get(sid, {}).get("status") in ("ok", "skip")]'),
    ("M8 首条定类改为末条（§5 优先级失效）",
     "tools/scoring/decide.py",
     '    first_v = next((o for o in outcomes if o.severity == "hard" and o.result == R_VIOLATED), None)',
     '    first_v = next((o for o in reversed(outcomes) if o.severity == "hard" and o.result == R_VIOLATED), None)'),
]

COPY_ITEMS = ["tools", "schemas", "engine", "samples"]   # samples/ 必须在：
# test_score_units 现在会自行重建 pilot 卷，需要卡片与 plan（期望指纹的单一来源）


def run_units(tmp):
    script = os.path.join(tmp, "tools", "tests", "test_score_units.py")
    out = subprocess.run([sys.executable, script], capture_output=True, timeout=300)
    stdout = (out.stdout or b"").decode("utf-8", "replace")
    stderr = (out.stderr or b"").decode("utf-8", "replace")
    reds = re.findall(r"FAIL\s+(\S+)", stdout)
    if out.returncode not in (0, 1):  # 崩了不是红，是环境异常
        print("   [units 异常退出]", (stderr or stdout)[-300:])
    return out.returncode, reds


def main() -> int:
    verbose = "-v" in sys.argv
    # 基线：干净副本必须绿（证明"红"来自变异而非复制/环境）
    base = tempfile.mkdtemp(prefix="okb_mut_base_")
    for it in COPY_ITEMS:
        shutil.copytree(os.path.join(ROOT, it), os.path.join(base, it),
                        ignore=shutil.ignore_patterns("__pycache__"))
    code, _ = run_units(base)
    shutil.rmtree(base, ignore_errors=True)
    if code != 0:
        print("❌ 基线副本未过（复制体不完整），变异测试结果无效")
        return 2
    print("基线副本绿 ✅（变异红只能来自变异本身）\n")

    gaps = []
    for name, rel, old, new in MUTS:
        tmp = tempfile.mkdtemp(prefix="okb_mut_")
        try:
            for it in COPY_ITEMS:
                shutil.copytree(os.path.join(ROOT, it), os.path.join(tmp, it),
                                ignore=shutil.ignore_patterns("__pycache__"))
            target = os.path.join(tmp, rel)
            text = open(target, encoding="utf-8").read()
            n = text.count(old)
            if n != 1:
                print(f"⚠ {name}\n    锚点命中 {n} 次（需恰好 1）——变异未生效，检查 MUTS 文本")
                gaps.append((name, "patch-anchor-broken"))
                continue
            open(target, "w", encoding="utf-8", newline="\n").write(text.replace(old, new))
            code, reds = run_units(tmp)
            caught = code != 0 and reds
            mark = "🔴 被抓" if caught else "🟢 存活=守卫盲区!"
            print(f"{mark}  {name}   {('← ' + ','.join(sorted(set(reds)))) if reds else ''}")
            if verbose and caught:
                for x in sorted(set(reds)):
                    print(f"      · {x}")
            if not caught:
                gaps.append((name, "survived"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if gaps:
        print(f"❌ 变异测试失败：{len(gaps)}/{len(MUTS)} 个变异存活/失效")
        for g in gaps:
            print("   ", g)
        return 1
    print(f"✅ {len(MUTS)}/{len(MUTS)} 注入缺陷全部被断言集抓红——守卫具备可失败性")
    return 0


if __name__ == "__main__":
    sys.exit(main())
