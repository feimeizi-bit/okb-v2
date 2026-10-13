#!/usr/bin/env python3
"""常驻自检：面向用户的报错必须带上异常原因，不许只印异常类型。

起因（实测踩过两次，各浪费一轮排查）：`score-vnext` 只印 `Four-layer run rejected: ValueError`，
真实原因是 `scoped evidence requires v2 outcomes`；`report-multirun` 同样只印类型。
这类"把原因吞掉"的 except 在 A 的真机上会直接把协作方卡死，且没法从回执里判断。
"""
import re
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
# 已经带原因的安全形态：f"{type(exc).__name__}: {exc}" / str(exc) / '%s: %s' % (type(exc).__name__, exc)
# 带原因的写法与引号种类无关，只要异常对象本身被格式化进去即可
REASON_OK = ('{exc}', 'str(exc)', '__name__, exc)')


def offending():
    bad = []
    for path in sorted(TOOLS.rglob("*.py")):
        parts = set(path.parts)
        if '.git' in parts or 'tests' in parts or '_archived' in parts:
            continue
        for n, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            s = line.strip()
            if not s.startswith('print(') or 'type(exc).__name__' not in s:
                continue
            if any(ok in s for ok in REASON_OK):
                continue
            bad.append('%s:%d  %s' % (path.relative_to(TOOLS), n, s[:110]))
    return bad


class ErrorReasonTests(unittest.TestCase):
    def test_no_print_swallows_the_exception_reason(self):
        bad = offending()
        self.assertEqual(bad, [], "以下 print 只印异常类型、丢了原因：\n" + "\n".join(bad))

    def test_detector_itself_can_fail(self):
        """防回归：检测器必须真的能抓到这种写法（否则上面那条测试是空跑）。"""
        broken = "print('Tool rejected: '+type(exc).__name__, file=sys.stderr)"
        self.assertFalse(any(ok in broken for ok in REASON_OK), "检测器漏判：这种写法必须变红")
        for good in ("print('Tool rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)",
                     'print("rejected: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)',
                     'print(f"failed: {type(exc).__name__}: {exc}", file=sys.stderr)'):
            self.assertTrue(any(ok in good for ok in REASON_OK), '检测器误报: ' + good)


if __name__ == "__main__":
    unittest.main(exit=False)
