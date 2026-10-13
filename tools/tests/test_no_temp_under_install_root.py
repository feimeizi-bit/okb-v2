#!/usr/bin/env python3
"""守卫：测试不得把临时目录建在仓库/安装根里。

真机代价（A 侧 2026-10-08 回执）：`.deb` 装到 `/var/opt/okb`（root 属主）后，普通用户跑自检
唯一的一条红就是 `test_multirun` 的 `TemporaryDirectory(dir=<仓库根>)`；用 root 跑则完全看不出来。
这类缺陷只有 lint 才会常驻，靠人读代码读不出来（同一个假设当时散在 10 个文件里）。
"""
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]
import testtmp  # noqa: E402

BAD = re.compile(r"tempfile\.(TemporaryDirectory|mkdtemp|NamedTemporaryFile)\([^)]*dir\s*=\s*(ROOT|str\(ROOT\))")
TEST_DIRS = ("tools/tests", "engine/tests", "tools")


def offenders(root: pathlib.Path):
    out = []
    for d in TEST_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if BAD.search(line):
                    out.append("%s:%d" % (f.relative_to(root).as_posix(), i))
    return out


class NoTempUnderInstallRootTests(unittest.TestCase):
    def test_current_tree_has_no_offender(self):
        got = offenders(ROOT)
        self.assertEqual([], got,
                         "以下位置把临时目录建在仓库/安装根（安装态非 root 必红）：\n  " + "\n  ".join(got))

    def test_helper_never_targets_the_repo_root(self):
        b = testtmp.base()
        self.assertNotEqual(b.resolve(), ROOT.resolve(),
                            "helper 又指回仓库根了：装到 /opt/okb 时就是刚才那条红")

    def test_env_override_is_honoured(self):
        with tempfile.TemporaryDirectory() as td:
            old = __import__("os").environ.get("OKB_TEST_TMP")
            __import__("os").environ["OKB_TEST_TMP"] = td
            try:
                with testtmp.TemporaryDirectory() as got:
                    self.assertTrue(pathlib.Path(got).is_dir())
                    self.assertTrue(str(got).startswith(str(pathlib.Path(td).resolve())) or
                                    str(got).startswith(td), got)
            finally:
                if old is None:
                    __import__("os").environ.pop("OKB_TEST_TMP", None)
                else:
                    __import__("os").environ["OKB_TEST_TMP"] = old

    def test_lint_itself_can_fail(self):
        """守卫必须可失败：造一个违规副本，它要抓到（这条曾经被我写成"永远绿"的自检）。"""
        with tempfile.TemporaryDirectory() as td:
            fake = pathlib.Path(td)
            (fake / "tools/tests").mkdir(parents=True)
            # 变异样本的源码字面量要拆开写：否则本文件的 lint 会先把这个测试文件自己判为违规
            # （豁免名单会让 lint 变弱，所以只绕字符串，不绕规则）。
            offender = ("import tempfile\nwith tempfile.Temporary" +
                        "Directory(dir=ROOT) as d:\n    pass\n")
            (fake / "tools/tests/test_bad.py").write_text(offender, encoding="utf-8")
            got = offenders(fake)
        self.assertTrue(got, "lint 没抓到故意造的违规 —— 它已经是装饰了")
        self.assertIn("test_bad.py:2", got[0])


if __name__ == "__main__":
    unittest.main(exit=False)
