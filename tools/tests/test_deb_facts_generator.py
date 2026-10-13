#!/usr/bin/env python3
"""包事实表的可复现性与"宁缺不旧"守卫。

背景：appendix 那张事实表曾经整片手写，本轮审计发现它停在一次旧构建上
（350,764 字节 / sha 691e61… / 215 成员 / 20 子命令），而真实包早就不是那个东西。
现在表格由 tools/make_deb_appendix_facts.py 从"包 + 旁证 + 回执"量出来，
这张测试保证：① 表与重算一致；② 回执过期时必须拒绝生成，而不是沿用旧数字。
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
TARGET = ROOT / "deploy/RUN_ON_OPENKYLIN-appendix.md"
GEN = ROOT / "tools/make_deb_appendix_facts.py"


class DebFactsGeneratorTests(unittest.TestCase):
    def test_facts_block_matches_recomputation(self):
        if not (ROOT / "dist").joinpath("okb_0.1.0_all.deb").is_file():
            self.skipTest("尚未构建 .deb（干净环境跳过，不假红）")
        if not (ROOT / "dist/okb_0.1.0_all.members.txt").is_file():
            self.skipTest("缺少 build-deb.sh 落下的成员旁证")
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "block.md"
            p = subprocess.run([sys.executable, str(GEN), str(out)], cwd=str(ROOT),
                               capture_output=True, encoding="utf-8", errors="replace", timeout=300)
            self.assertEqual(p.returncode, 0, (p.stdout or '') + (p.stderr or ''))
            fresh = out.read_text(encoding="utf-8")
        doc = TARGET.read_text(encoding="utf-8")
        self.assertIn("DEB-FACTS:BEGIN", doc, "事实表必须有标记区，否则无法核对是否过期")
        inrepo = doc.split("DEB-FACTS:BEGIN", 1)[1].split("DEB-FACTS:END", 1)[0]
        body = fresh.split("DEB-FACTS:BEGIN", 1)[1].split("DEB-FACTS:END", 1)[0]
        self.assertEqual(inrepo.strip(), body.strip(),
                         "commit 里的包事实与重算不一致：跑 tools/make_deb_appendix_facts.py")

    def test_stale_receipt_is_rejected_not_reused(self):
        """回执项数和当前自检脚本不一致时必须失败——这条抓到过 pass=21 的旧回执。"""
        import make_deb_appendix_facts as G
        real = G.verify_check_count()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "deploy").mkdir()
            # 让 main() 能走到"读回执"这一步：造出最小的 dist/ + 旁证 + deploy 脚本
            (root / "dist").mkdir()
            (root / "dist" / "okb_0.1.0_all.deb").write_bytes(b"dummy deb bytes")
            (root / "dist" / "okb_0.1.0_all.members.txt").write_text("./opt/okb/a.py\n", encoding="utf-8")
            (root / "deploy" / "openkylin-verify.sh").write_text(
                (ROOT / "deploy/openkylin-verify.sh").read_text(encoding="utf-8"), encoding="utf-8")
            notes = root / "notes"
            notes.mkdir()
            stale = dict(os="Ubuntu 24.04.3 LTS", python="3.12.3",
                         passed=real - 3, failed=0, checks=[dict(name="delivery_chain")])
            (notes / "install-state-selfcheck-2026-10-05.json").write_text(
                json.dumps(stale), encoding="utf-8")
            G.ROOT = root                       # 把脚本的根指到临时目录
            G.TARGET = root / "appendix.md"
            with self.assertRaises(SystemExit) as got:
                G.main([str(root / "block.md")])
            msg = str(got.exception)
            self.assertIn("旧回执", msg)
            self.assertIn(str(real), msg)

    def test_missing_sidecar_fails_loudly(self):
        import make_deb_appendix_facts as G
        with tempfile.TemporaryDirectory() as td:
            fake = pathlib.Path(td) / "okb_9.9.9_all.deb"
            fake.write_bytes(b"not a deb")
            with self.assertRaises(SystemExit) as got:
                G.deb_members(fake)
        self.assertIn("members.txt", str(got.exception),
                      "旁证缺失必须明确报错，不能悄悄沿用上一次数字")

    def test_generated_rows_cover_the_stale_fields(self):
        doc = TARGET.read_text(encoding="utf-8")
        block = doc.split("DEB-FACTS:BEGIN", 1)[-1].split("DEB-FACTS:END", 1)[0]
        for row in ("大小", "sha256", "包成员", "子命令", "自检项数", "未取得"):
            self.assertIn(row, block, "事实表缺行: " + row)
        self.assertRegex(block, r"真机（openKylin）验证 \| \*\*未取得\*\*",
                        "代理环境证据必须自己声明它不是真机")


if __name__ == "__main__":
    unittest.main(exit=False)
