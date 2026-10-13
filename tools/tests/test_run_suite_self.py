#!/usr/bin/env python3
"""测试跑器自己必须有牙：不许"什么都没跑却全绿"。

起因（本轮真实发生）：我用脚本批量改 run_suite.py，把"汇总打印 + 凭据写入 + 返回码"整段误删，
结果套件不再报总数、`--json` 不再写凭据，而**退出码永远是 0**。只看 rc 的人会把这种状态当成
"全绿"。这几条测试就是为了让它立刻变红。
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = sys.executable


def run(argv, timeout=900):
    return subprocess.run([PY, str(ROOT / 'tools' / 'run_suite.py')] + argv, cwd=str(ROOT),
                          capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)


class RunSuiteSelfTests(unittest.TestCase):
    def test_summary_line_is_printed(self):
        p = run(['w212'])
        self.assertRegex(p.stdout or '', r'套件 \d+ 个，用例 \d+ 个，失败 \d+ 个',
                         '跑器必须打印可解析的汇总行（缺它说明结果判定逻辑被删了）')

    def test_empty_filter_is_not_green(self):
        p = run(['zzz_no_such_suite_zzz'])
        self.assertEqual(p.returncode, 2, (p.stdout or '')[-300:] + (p.stderr or '')[-300:])
        self.assertIn('拒绝以全绿收场', p.stderr or '')

    def test_json_credential_actually_written(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / 'cred.json'
            p = run(['w212', '--json', str(out)])
            self.assertEqual(p.returncode, 0, (p.stdout or '')[-400:])
            self.assertTrue(out.is_file(), '--json 必须真的写出凭据文件，而不是只在参数里出现')
            cred = json.loads(out.read_text(encoding='utf-8'))
            for k in ('format', 'suites', 'cases', 'failures', 'newest_test_mtime'):
                self.assertIn(k, cred, k)
            self.assertGreaterEqual(cred['cases'], 1)

    def test_double_js_report_line_not_printed_when_skipped(self):
        """跳过 JS 时不得再伪造一行 PASS（本轮就出现过 SKIP+PASS 同时打印）。"""
        src = (ROOT / 'tools' / 'run_suite.py').read_text(encoding='utf-8')
        self.assertIn('if not js_skipped', src,
                      'js_skipped 时仍会打印 PASS 行，SKIP 与 PASS 并存会让按行判定的脚本被骗')

    def test_a_failing_suite_makes_the_run_red(self):
        bogus = ROOT / 'tools' / 'tests' / 'test_zz_selfcheck_bogus.py'
        bogus.write_text(
            "import unittest\n\n\n"
            "class Bogus(unittest.TestCase):\n"
            "    def test_always_fails(self):\n"
            "        self.assertEqual(1, 2)\n\n\n"
            'if __name__ == "__main__":\n'
            "    unittest.main(exit=False)\n",
            encoding='utf-8', newline='\n')
        try:
            p = run(['test_zz_selfcheck_bogus'])
            self.assertEqual(p.returncode, 2, '失败套件没有让整体变红：跑器已失去判定能力')
            self.assertIn('test_zz_selfcheck_bogus.py', p.stdout or '')
            self.assertRegex(p.stdout or '', r'失败 1 个')
        finally:
            bogus.unlink()


if __name__ == "__main__":
    unittest.main(exit=False)
