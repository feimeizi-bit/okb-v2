#!/usr/bin/env python3
"""okb · 六维雷达 SVG 渲染器（独立小工具：只消费 report.py 的 json，不动 v4.1 报告层）。

    python3 tools/radar.py --in build/report-x.json --out build/radar-x.svg [--per-agent]

赛题交付 d/e 的「多维对比雷达图」落点。零依赖（纯 SVG 手写）。
设计口径：每个 agent 一个多边形（六维 acc），低置信维画 ⚠ 标记且**照常出图**
（雷达展示的是可读信号；总分拒算的口径由 report 层管）。无数据维=半径 0。
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import os
from pathlib import Path

DIMS = ["long_term_retention", "memory_recall", "dynamic_update",
        "similar_discrimination", "boundary_recognition", "task_reuse"]
ZH = {"long_term_retention": "长期保持", "memory_recall": "记忆调用", "dynamic_update": "动态更新",
      "similar_discrimination": "相近区分", "boundary_recognition": "边界识别", "task_reuse": "任务复用"}
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf"]


def pt(cx, cy, r, k, total=6):
    ang = -math.pi / 2 + 2 * math.pi * k / total
    return cx + r * math.cos(ang), cy + r * math.sin(ang)


def render(doc, out: Path) -> int:
    runs = doc.get("runs") or []
    if not runs:
        print("无 runs，拒画", file=sys.stderr)
        return 1
    W, H, CX, CY, R = 760, 620, 300, 320, 220
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="system-ui,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="white"/>',
         f'<text x="{CX}" y="34" font-size="18" text-anchor="middle">okb 六维雷达（规则口径 acc，'
         f'{doc.get("generated_by","report")}）</text>']
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(CX, CY, R * ring, k) for k in range(6)))
        s.append(f'<polygon points="{pts}" fill="none" stroke="#cfd4da" stroke-width="1"/>')
        s.append(f'<text x="{CX+4}" y="{CY-R*ring+4:.0f}" font-size="10" fill="#8a919b">{int(ring*100)}%</text>')
    for k, dim in enumerate(DIMS):
        x, y = pt(CX, CY, R + 34, k)
        anchor = "middle" if abs(x - CX) < 12 else ("start" if x > CX else "end")
        s.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="13" text-anchor="{anchor}" fill="#333">{ZH[dim]}</text>')
        x2, y2 = pt(CX, CY, R, k)
        s.append(f'<line x1="{CX}" y1="{CY}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#cfd4da"/>')
    agents = []
    for r in runs:
        ag = r.get("agent") or r.get("run_id") or f"run{len(agents)}"
        if ag not in agents:
            agents.append(ag)
    for i, ag in enumerate(agents[:6]):
        dims = {}
        for r in runs:
            rag = r.get("agent") or r.get("run_id")
            if rag == ag:
                dims.update(r.get("dims") or {})
        col = PALETTE[i % len(PALETTE)]
        pts, marks = [], []
        for k, dim in enumerate(DIMS):
            d = dims.get(dim) or {}
            v = d.get("acc")
            v = 0.0 if v is None else float(v)
            x, y = pt(CX, CY, R * max(0.0, min(1.0, v)), k)
            pts.append((x, y))
            if d.get("low_confidence"):
                mx, my = pt(CX, CY, R * max(0.0, min(1.0, v)) + 14, k)
                marks.append(f'<text x="{mx:.1f}" y="{my:.1f}" font-size="12" fill="{col}">⚠</text>')
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        s.append(f'<polygon points="{p}" fill="{col}" fill-opacity="0.18" stroke="{col}" stroke-width="2"/>')
        s.extend(marks)
        ly = 60 + i * 22
        s.append(f'<rect x="580" y="{ly-12}" width="14" height="14" fill="{col}" fill-opacity="0.5"/>')
        s.append(f'<text x="600" y="{ly}" font-size="13">{ag}</text>')
    s.append(f'<text x="580" y="{60 + min(len(agents),6)*22 + 8}" font-size="11" fill="#8a919b">'
             f'⚠=低置信维（§7 口径 3；雷达照画，总分由报告层拒算）</text>')
    s.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".svg.t")
    tmp.write_text("\n".join(s), encoding="utf-8", newline="\n")
    os.replace(tmp, out)
    print(f"✅ {out}（{len(agents)} agent × 6 维）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="report.py 产出的 .json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return render(json.loads(Path(a.inp).read_text(encoding="utf-8")), Path(a.out))


if __name__ == "__main__":
    sys.exit(main())
