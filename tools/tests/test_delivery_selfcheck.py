#!/usr/bin/env python3
"""Rebuild the frozen T0 rule-only delivery in isolation (no live calls).

Explicit --no-judged keeps later live-judge artifacts out of this rule snapshot.
Requires the original T0 run/evidence/raw in the checkout or VM sync tree.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/run_v02_first"
DELIVERY = ROOT / "deliveries/run_v02_first"
from paper_fixture import frozen_sha256   # 期望指纹单一来源，不再手抄第二份
PAPER_SHA = frozen_sha256()
PRERE = ROOT / "notes/verdicts-v02-first-PRERE-G1.jsonl"
PRERE_SHA = "af1058297fbe6508afaed7ad0b06ac4d8d443e663f1dbe2e6829d78bf7e412fe"


def run(script, *args, cwd=ROOT):
    subprocess.run([sys.executable, str(ROOT / script), *map(str, args)],
                   cwd=cwd, check=True, timeout=120)


def check_equal(actual, expected):
    assert actual.read_bytes() == expected.read_bytes(), f"delivery mismatch: {actual} != {expected}"


def main():
    # runs/ 不在同步范围内（deploy/sync-to-vm.ps1 只带 engine/tools/samples/deploy/docs/notes/okb/
    # deliveries），VM 上这条曾是裸 FileNotFoundError。缺输入必须说"跳过"，不能说"失败"，
    # 也不能假装通过——本仓库对这两者的区分是有测试的（test_run_suite_self）。
    missing = [str(x.relative_to(ROOT)) for x in (RUN, DELIVERY, PRERE) if not x.exists()]
    if missing:
        print("SKIP 交付自检（缺维护者历史产物：%s；这些目录不在 VM 同步范围内，"
              "在有 runs/ 的开发机上会真跑逐字节核对）" % "、".join(missing))
        return 0
    assert hashlib.sha256(PRERE.read_bytes()).hexdigest() == PRERE_SHA, "historical PRERE-G1 archive drift"
    with tempfile.TemporaryDirectory(prefix="okb-delivery-") as td:
        tmp = Path(td)
        paper = tmp / "paper-v0.2.jsonl"
        run("tools/build_paper.py", "samples/pilot-v0.2.yaml", "-o", paper)
        assert hashlib.sha256(paper.read_bytes()).hexdigest() == PAPER_SHA, "frozen paper drift"
        replay = tmp / "runs" / RUN.name
        shutil.copytree(RUN, replay)
        run("tools/score.py", "--run-dir", replay, "--paper", paper,
            "--out", replay / "verdicts.jsonl")
        check_equal(replay / "verdicts.jsonl", RUN / "verdicts.jsonl")
        check_equal(replay / "verdicts.jsonl", DELIVERY / "verdicts.jsonl")
        prefix = tmp / "report-run_v02_first"
        # Keep the recorded relative provenance path; do not normalize JSON fields.
        run("tools/report.py", "--runs", "runs/run_v02_first", "--paper", paper,
            "--out", prefix, "--sensitivity", "--trace", "2", "--no-judged", cwd=tmp)
        for suffix in (".md", ".json"):
            check_equal(prefix.with_suffix(suffix), DELIVERY / (prefix.name + suffix))
        svg = tmp / "radar-run_v02_first.svg"
        run("tools/radar.py", "--in", prefix.with_suffix(".json"), "--out", svg)
        check_equal(svg, DELIVERY / svg.name)
    print("PASS: fresh paper + rule score + report + radar match T0 delivery byte-for-byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
