#!/usr/bin/env python3
"""常驻守卫：仓库里每个 .py 都必须能被编译（语法级）。

为什么需要：本轮我用脚本批量改源码，两次把文件改坏——一次缩进错位（mock_policy.py）、
一次丢掉返回注解（simulate.py）。它们都只在**别的测试间接 import 到**时才炸，报错形式是
"无成功标记"，看起来像环境抖动，实际是我自己写的语法错误。这条测试让它在第一时间直接变红，
并且不需要跑任何业务逻辑。
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP_PARTS = {'.git', '__pycache__', '_archived', 'node_modules', 'dist'}


def python_files():
    for p in sorted(ROOT.rglob('*.py')):
        if SKIP_PARTS & set(p.parts):
            continue
        yield p


class AllSourcesCompileTests(unittest.TestCase):
    def test_every_python_file_compiles(self):
        broken = []
        for p in python_files():
            src = p.read_text(encoding='utf-8', errors='replace')
            try:
                compile(src, str(p), 'exec')
            except SyntaxError as exc:
                broken.append('%s:%s %s' % (p.relative_to(ROOT), exc.lineno, exc.msg))
        self.assertEqual(broken, [], "以下文件有语法错误：\n" + "\n".join(broken))

    def test_guard_actually_detects_damage(self):
        """这条测试自身必须可失败：喂一段坏代码，compile 一定要抛。"""
        with self.assertRaises(SyntaxError):
            compile('def f(x)\n    return x\n', '<bad>', 'exec')


if __name__ == "__main__":
    unittest.main(exit=False)
