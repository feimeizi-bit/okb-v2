#!/usr/bin/env python3
"""路径展示用的相对化 helper —— 跨盘符不许炸。

起因：测试的临时目录改到系统临时目录（`C:/...`）后，`tools/simulate.py` 里
`os.path.relpath(policy_path, ROOT)` 在 Windows 上直接 `ValueError: path is on mount 'C:',
start on mount 'D:'`，连带 20 个用例红。真实场景同样会撞上（仓库在 D:、`%TEMP%` 在 C: 是很常见的
开发机布局），所以修调用方，而不是把临时目录塞回仓库根（那是 A 侧真机自检红的根因）。
"""
from __future__ import annotations

import os


def rel_display(path, start=None) -> str:
    """能相对就相对（正斜杠），不能相对（跨盘/跨根）就退回原路径。绝不抛。"""
    s = str(start if start is not None else os.getcwd())
    try:
        return os.path.relpath(str(path), s).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def rel_or_none(path, start=None):
    """需要"要么相对、要么明确失败"的场合：跨盘返回 None，让调用方决定。"""
    s = str(start if start is not None else os.getcwd())
    try:
        return os.path.relpath(str(path), s).replace("\\", "/")
    except ValueError:
        return None
