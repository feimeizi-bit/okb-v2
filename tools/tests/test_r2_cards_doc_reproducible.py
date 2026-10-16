#!/usr/bin/env python3
"""docs/paper-r2-cards.md 必须能由产物重新渲染出同一份字节（防止页面变成"我手写的说法"）。

这类漂移本轮就发生过一次：一页纸用一次性 heredoc 生成、脚本没留在仓库，之后就再也无法验证
它是否还和题卡/凭据一致。现在生成器是 tools/make_r2_cards_doc.py，这里做逐字节比对。
"""
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/paper-r2-cards.md"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


class R2CardsDocTests(unittest.TestCase):
    def test_page_is_reproducible_from_artefacts(self):
        if not DOC.is_file():
            self.skipTest("一页纸尚未生成")
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "page.md"
            proc = subprocess.run([sys.executable, str(ROOT / "tools/make_r2_cards_doc.py"),
                                   "--out", str(out)], cwd=str(ROOT), capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=300)
            self.assertEqual(proc.returncode, 0, (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:])
            self.assertEqual(sha(out), sha(DOC),
                             "committed 一页纸与重新渲染结果不一致：请跑 tools/make_r2_cards_doc.py")

    def test_page_quotes_live_numbers(self):
        """页面里的关键数字必须来自当前凭据，不能是当年快照。"""
        import json
        if not (ROOT / "notes/paper-r2-candidate.json").is_file():
            self.skipTest("r2 凭据未生成")
        cred = json.loads((ROOT / "notes/paper-r2-candidate.json").read_text(encoding="utf-8"))
        att = json.loads((ROOT / "notes/baseline-attribution-mock-r2.json").read_text(encoding="utf-8"))
        page = DOC.read_text(encoding="utf-8")
        self.assertIn(cred["paper_sha256"][:12], page)
        self.assertIn(cred["plan_sha256"][:12], page)
        self.assertIn(str(cred["scope_counts"]["memory_before"]), page)
        self.assertIn(json.dumps(att["attribution_counts"], ensure_ascii=False), page)
        self.assertIn("capture_gap", page, "五态图例缺失")


if __name__ == "__main__":
    unittest.main(exit=False)
