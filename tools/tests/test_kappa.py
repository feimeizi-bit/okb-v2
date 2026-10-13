#!/usr/bin/env python3
"""κ 工具链自测（P2-2 出口①：合成标签手算对拍）。python tools/tests/test_kappa.py"""
from __future__ import annotations
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import kappa as K  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="okb_kappa_")

    # 经典教科书 2×2：50 被试，A/B 各评 yes/no
    #            B_yes B_no
    #   A_yes     20   10
    #   A_no      15    5
    # po = 25/50 = .50; pe = (.6*.7) + (.4*.3) = .42+.12 = .54
    # κ = (.50-.54)/(1-.54) = -.04/.46 ≈ -0.086957
    pairs = [("yes", "yes")] * 20 + [("yes", "no")] * 10 + [("no", "yes")] * 15 + [("no", "no")] * 5
    res = K.cohen(pairs, cats=["yes", "no"])
    check("κ 教科书例 = -0.0870(±1e-4)", abs(res[0] - (-0.04 / 0.46)) < 1e-4)
    check("po=0.50 / pe=0.54", abs(res[1] - 0.5) < 1e-9 and abs(res[2] - 0.54) < 1e-9)

    # 全一致 → 1.0
    same = [("remembered", "remembered")] * 5 + [("confused", "confused")] * 5
    check("全一致 κ=1.0", abs(K.cohen(same)[0] - 1.0) < 1e-9)

    # 全错开（两对角块）→ κ = -? 至少 < 0
    anti = [("remembered", "omitted")] * 5 + [("omitted", "remembered")] * 5
    check("全错开 κ<0", K.cohen(anti)[0] < 0)

    # CLI 端到端 + 二分类视角
    a, b = os.path.join(tmp, "A.jsonl"), os.path.join(tmp, "B.jsonl")
    ra = [{"row_key": f"r{i}", "label": l} for i, l in enumerate(
        ["remembered", "remembered", "omitted", "confused", "wrongly_persisted",
         "remembered", "omitted", "confused"])]
    rb = [{"row_key": f"r{i}", "label": l} for i, l in enumerate(
        ["remembered", "remembered", "omitted", "remembered", "wrongly_persisted",
         "confused", "omitted", "confused"])]
    write_jsonl(a, ra); write_jsonl(b, rb)
    out = os.path.join(tmp, "rep.md")
    rc = K.main([a, b, "--out", out])
    text = open(out, encoding="utf-8").read()
    # 手算：po=6/8=.75；边际 A: rem3,om2,cf2,wp1 ; B: rem4,om2,cf2
    pe = (3/8)*(3/8) + (2/8)*(2/8) + (2/8)*(2/8) + (1/8)*(1/8)   # B 边际=rem3/om2/cf2/wp1
    kv = (0.75 - pe) / (1 - pe)
    check("CLI exit 0", rc == 0)
    check(f"报告含五分类 κ≈{kv:.4f}", f"{kv:.4f}" in text)
    check("不一致清单列出 r3", "r3" in text and "混淆矩阵" in text)
    binv = K.cohen([("remembered" if x == "remembered" else "non",
                     "remembered" if y == "remembered" else "non") for x, y in
                    zip([r["label"] for r in ra], [r["label"] for r in rb])],
                  cats=["remembered", "non"])
    check("二分类视角单独可算(>0)", binv[0] > 0.3)

    print("\n" + (f"❌ 失败 {len(FAILS)}: {FAILS}" if FAILS else "✅ κ 工具链自测全部通过"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
