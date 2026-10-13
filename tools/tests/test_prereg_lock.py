#!/usr/bin/env python3
"""预注册锁完整性：两代 paper 的 sha 必须可由现源文件重建复现（防「锁定物漂移/构建回归」）。
覆盖 momus/review 双发现的两个洞：build 路径 legacy 降级、锁定 sha 与源漂移。
用法：python tools/tests/test_prereg_lock.py
"""
from __future__ import annotations
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

PAIRS = [
    ("samples/pilot-v0.1.yaml", "notes/c2-protocol-2026-09-21.md", r"锁定信息：pilot\.jsonl sha256 = ([0-9a-f]{64})"),
    ("samples/pilot-v0.2.yaml", "notes/c2-protocol-v0.2.md", r"锁定信息：sha256\(build/paper-v0\.2\.jsonl\) = ([0-9a-f]{64})"),
]


def main() -> int:
    fails = []
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="okb_lock_"))
    for src, doc, pat in PAIRS:
        locked = re.search(pat, (ROOT / doc).read_text(encoding="utf-8"))
        if not locked:
            fails.append(f"{doc}: 找不到锁定的 sha 行")
            continue
        out = tmp / (pathlib.Path(src).stem + ".jsonl")
        r = subprocess.run([sys.executable, "tools/build_paper.py", src, "-o", str(out)],
                           capture_output=True, cwd=ROOT)
        if r.returncode != 0:
            fails.append(f"{src}: 干净构建失败（exit {r.returncode}）——v0.1 legacy 降级回归？")
            continue
        got = hashlib.sha256(out.read_bytes()).hexdigest()
        if got != locked.group(1):
            fails.append(f"{src}: sha 漂移 {got[:12]} ≠ 锁定 {locked.group(1)[:12]}（锁后改样本未重锚定）")
        else:
            print(f"PASS  {src} → {got[:12]} == 锁定值")
    print("\n✅ 预注册锁完整（两代 paper 均可由源重建复现）" if not fails else "❌\n  " + "\n  ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
