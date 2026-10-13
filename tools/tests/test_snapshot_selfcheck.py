#!/usr/bin/env python3
"""快照自证链上锁（momus：该链连破两轮 7b063b0/a2e4977，只有断言能止）。

核对三件事（build 产物存在时全核，不存在则跳过并提示重跑命令）：
1) 头部声明的 16-hex sha 与 sha256(去注释头正文) 一致；
2) 去头正文与 build/report-mock-v0.2.md 逐字节一致；
3) 正文含 v4.1 世代标记（表头/公示标题），防「只换头不贴正文」复发。
"""
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAP = ROOT / "notes" / "report-mock-v0.2-snapshot.md"
GEN = ROOT / "build" / "report-mock-v0.2.md"


def main():
    t = SNAP.read_text(encoding="utf-8")
    i = t.find("# okb 成绩单")
    assert i > 0, "快照缺正文起点 '# okb 成绩单'"
    body = t[i:]
    head = t[:i]
    declared = head.split("前16 = ")[1][:16]
    real = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    assert declared == real and len(declared) == 16, f"头部 sha 自证不符 {declared} vs {real}"
    for mark in ("tools/report.py v4.1", "行级剔除（带归因", "样本·"):
        assert mark in body, f"正文缺 v4.1 世代标记：{mark}"
    if not GEN.exists():
        print(f"SKIP 逐字节核对（build 产物不存在，先跑 c2_run --suite v0.2 --skip-ablate "
              f"+ report.py，见快照头命令）；头↔正文自证已过：{real}")
        return 0
    g = GEN.read_text(encoding="utf-8")
    assert body == g, "快照正文与 build 生成物不一致（重拼断了或版本落后）"
    print(f"✅ 快照自证链全核通过（sha={real}，正文==build 生成物）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
