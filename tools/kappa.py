#!/usr/bin/env python3
"""Cohen's κ / 混淆矩阵 / 不一致案例（P2-2）。一行一 JSON 标签文件：{"row_key","label","note"}。
用法：python tools/kappa.py A.jsonl B.jsonl [--out report.md]
"""
from __future__ import annotations
import argparse
import collections
import json
import sys

CATS = ["remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"]


def load(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["row_key"]] = r
    return out


def cohen(pairs, cats=None):
    cats = cats or sorted({c for ab in pairs for c in ab})
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    ea = collections.Counter([a for a, _ in pairs])
    eb = collections.Counter([b for _, b in pairs])
    pe = sum((ea[c] / n) * (eb[c] / n) for c in cats)
    return None if pe == 1 else (po - pe) / (1 - pe), po, pe


def confusion(pairs):
    cats = CATS
    m = {a: collections.Counter() for a in cats}
    for a, b in pairs:
        m[a][b] += 1
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    A, B = load(args.a), load(args.b)
    keys = sorted(set(A) & set(B))
    if not keys:
        print("无重叠 row_key", file=sys.stderr); return 2
    pairs = [(A[k]["label"], B[k]["label"]) for k in keys]
    k5, po, pe = cohen(pairs)
    binp = [("remembered" if a == "remembered" else "non",
             "remembered" if b == "remembered" else "non") for a, b in pairs]
    kb, pob, peb = cohen(binp, cats=["remembered", "non"])
    conf = confusion(pairs)
    dis = [k for k in keys if A[k]["label"] != B[k]["label"]]
    lines = [f"# κ 报告  n={len(keys)}",
             f"- Cohen κ（五分类）= {k5:.4f}  (po={po:.3f}, pe={pe:.3f})",
             f"- Cohen κ（remembered|non 二分类）= {kb:.4f}",
             f"- 不一致 {len(dis)} 条: " + ", ".join(dis[:30]),
             "\n## 混淆矩阵（行=A，列=B）",
             "| A\\B | " + " | ".join(CATS) + " |",
             "|" + "---|" * (len(CATS) + 1)]
    for a in CATS:
        lines.append("| " + a + " | " + " | ".join(str(conf[a][b]) for b in CATS) + " |")
    if len(dis) <= 40:
        lines.append("\n## 不一致案例（A/B 注记）")
        for k in dis:
            lines.append(f"- {k}: A={A[k]['label']} ({A[k].get('note','')[:60]}) | B={B[k]['label']} ({B[k].get('note','')[:60]})")
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        open(args.out, "w", encoding="utf-8", newline="\n").write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
