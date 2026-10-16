#!/usr/bin/env python3
"""漂移台账表完整性自检（终验 P1 的永久回归位）：
notes/judge-drift-log.md 每个表块的 表头/分隔行 列数必须相等（GFM 渲染前提），
最新数据行列数与表头一致。防止「就地升级漏分隔行」类塌陷复发（fac5d70→0190aac 两次实伤）。
用法：python tools/tests/test_drift_log_table.py
"""
from __future__ import annotations
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def cells(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_delim(line: str):
    body = line.strip().strip("|")
    return bool(body) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in body.split("|"))


def main() -> int:
    txt = (ROOT / "notes" / "judge-drift-log.md").read_text(encoding="utf-8")
    lines = txt.split("\n")
    blocks, i, bad, warns = 0, 0, [], []
    while i < len(lines):
        if lines[i].startswith("| 时间"):
            blocks += 1
            h = len(cells(lines[i]))
            if i + 1 >= len(lines) or not is_delim(lines[i + 1]):
                bad.append(f"表头@{i+1} 下一行不是分隔行")
                i += 1
                continue
            d = len(cells(lines[i + 1]))
            if h != d:
                bad.append(f"表头/分隔列数不等 @{i+1}: {h} vs {d}")
            j = i + 2
            last = None
            while j < len(lines) and lines[j].startswith("|"):
                last = j
                j += 1
            if last is not None:
                lc = len(cells(lines[last]))
                if lc < h:
                    bad.append(f"最新数据行@{last+1} 列数 {lc} < 表头 {h}（GFM 会补空但语义错位）")
                elif lc != h:
                    warns.append(f"数据行@{last+1} 列数 {lc} > 表头 {h}（GFM 截断，史实行允许）")
            i = j
        else:
            i += 1
    if blocks == 0:
        print("❌ 未找到任何表块（格式漂移？）")
        return 1
    if bad:
        print("❌ 表完整性检查失败：")
        for b in bad:
            print("   -", b)
        return 1
    for w in warns:
        print("   ⚠", w)
    print(f"✅ 漂移台账 {blocks} 个表块：渲染前提（表头=分隔）与数据列下界全过（历史超宽行 {len(warns)} 条已示警）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
