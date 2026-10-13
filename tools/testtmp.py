#!/usr/bin/env python3
"""测试用临时目录：绝不在仓库/安装根里造，除非它确实可写。

起因（2026-10-08 A 侧真机回执）：openKylin VM 上以 .deb 安装态、普通用户 `okb` 跑自检，
24 项里 1 项红 —— `tools/tests/test_multirun.py` 用 `tempfile.TemporaryDirectory(dir=<仓库根>)`，
而 `/var/opt/okb` 属 root，普通用户建目录直接 `PermissionError`，连带整条链 39 个子用例全错。
查下来这不是孤例：仓里共 10 个测试文件有同一假设。A 的 root 身份跑（24/24 绿）恰好把它盖住了——
"用 root 跑一遍就全绿"正是这类缺陷最舒服的藏身处。

策略：优先 $OKB_TEST_TMP（明确指定）→ 系统临时目录（跨机、跨用户都成立）。
以前直写 ROOT 是为了"和仓库同一文件系统"，但那是个没验证过的偏好，代价是安装态必红。
"""
from __future__ import annotations

import os
import pathlib
import tempfile

_ENV = "OKB_TEST_TMP"


def _candidates() -> list[str]:
    """按可用性排序的候选基目录；系统临时目录永远兜底。"""
    out = []
    env = os.environ.get(_ENV)
    if env:
        out.append(env)
    out.append(tempfile.gettempdir())
    return out


def base() -> pathlib.Path:
    """实际会被使用的基目录（供断言/报错信息用）。"""
    for cand in _candidates():
        try:
            p = pathlib.Path(cand)
            if not p.is_dir():
                continue
            probe = p / (".okb-tmp-probe-%d" % os.getpid())
            probe.mkdir()
            probe.rmdir()
            return p
        except OSError:
            continue
    return pathlib.Path(tempfile.gettempdir())


def TemporaryDirectory(*args, **kw):
    """tempfile.TemporaryDirectory 的替代品：基目录一定可写。"""
    kw.pop("dir", None)             # 忽略调用方传入的 dir，防止又指回安装根
    last = None
    for cand in _candidates():
        try:
            if not pathlib.Path(cand).is_dir():
                continue
            return tempfile.TemporaryDirectory(*args, dir=cand, **kw)
        except (PermissionError, OSError) as exc:
            last = exc
            continue
    raise RuntimeError("找不到可写的临时目录（试过 %s）：%s" % (_candidates(), last))


def mkdtemp(*args, **kw):
    kw.pop("dir", None)
    last = None
    for cand in _candidates():
        try:
            if not pathlib.Path(cand).is_dir():
                continue
            return tempfile.mkdtemp(*args, dir=cand, **kw)
        except (PermissionError, OSError) as exc:
            last = exc
            continue
    raise RuntimeError("找不到可写的临时目录（试过 %s）：%s" % (_candidates(), last))
