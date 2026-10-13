"""okb 引擎 · field_path 解析（CONTRACT §15 #8（原 §12 #8））

与规则评分器共享的**同一份实现**：证据 payload 里的字段路径（`payload.value`、
`payload.entries[0].value`）由本模块解析。A 侧确认的规则：

- 语法：`.` 分隔的路径段 + `[n]` 数组下标
- 键名匹配前 trim、大小写不敏感
- 解析失败（路径不存在 / 类型不符）由调用方映射为 `task.status = env_error`

另外提供 `iter_strings()` / `set_in()`：脱敏流水线用它遍历 payload 里的所有字符串，
把命中值替换成占位符（先判定、后替换）。
"""
from __future__ import annotations

from typing import Any, Iterator

_SEG_CHARS = set(".[]")


class PathError(ValueError):
    """field_path 相关错误基类。"""


class PathSyntaxError(PathError):
    """路径语法错误（无法解析成段）。"""


class PathNotFound(PathError):
    """路径在当前对象上解析不到（不存在或类型不符）。"""


def parse_path(path: str) -> list[str | int]:
    """把 `a.b[0].c` 解析为 ["a", "b", 0, "c"]。

    语法从严：空段、未闭合的 `[`、非数字下标、路径首尾分隔符一律报 PathSyntaxError。
    """
    if not isinstance(path, str) or not path.strip():
        raise PathSyntaxError(f"路径为空：{path!r}")
    segments: list[str | int] = []
    buf: list[str] = []
    i = 0
    n = len(path)
    while i < n:
        ch = path[i]
        if ch == ".":
            if not buf:
                raise PathSyntaxError(f"路径出现空段（连续的 '.'？）：{path!r}")
            segments.append(_segment("".join(buf), path))
            buf.clear()
            i += 1
            if i >= n:
                raise PathSyntaxError(f"路径以 '.' 结尾：{path!r}")
        elif ch == "[":
            if buf:
                segments.append(_segment("".join(buf), path))
                buf.clear()
            elif not segments:
                raise PathSyntaxError(f"路径不能以 '[' 开头：{path!r}")
            j = path.find("]", i + 1)
            if j < 0:
                raise PathSyntaxError(f"未闭合的 '['：{path!r}")
            raw = path[i + 1 : j]
            if not raw.isdigit():
                raise PathSyntaxError(f"数组下标必须是数字：{path!r}")
            segments.append(int(raw))
            i = j + 1
            if i < n and path[i] == ".":
                i += 1
                if i >= n:
                    raise PathSyntaxError(f"路径以 '.' 结尾：{path!r}")
            elif i < n and path[i] not in ".[":
                raise PathSyntaxError(f"数组下标后缺少分隔符：{path!r}")
        else:
            buf.append(ch)
            i += 1
    if buf:
        segments.append(_segment("".join(buf), path))
    if not segments:
        raise PathSyntaxError(f"路径没有有效段：{path!r}")
    return segments


def _segment(raw: str, path: str) -> str:
    seg = raw.strip()
    if not seg:
        raise PathSyntaxError(f"路径段为空：{path!r}")
    return seg


def _match_key(obj: dict, key: str) -> str | None:
    """键名匹配：trim + 大小写不敏感；返回实际键名。"""
    if key in obj:
        return key
    target = key.strip().lower()
    for k in obj:
        if isinstance(k, str) and k.strip().lower() == target:
            return k
    return None


def resolve(obj: Any, path: str | list[str | int]) -> Any:
    """按路径取值；失败抛 PathNotFound。"""
    segments = parse_path(path) if isinstance(path, str) else path
    cur = obj
    for idx, seg in enumerate(segments):
        walked = ".".join(str(s) for s in segments[: idx + 1])
        if isinstance(seg, int):
            if not isinstance(cur, (list, tuple)) or seg >= len(cur):
                raise PathNotFound(f"路径 {walked!r} 无法解析（数组越界或类型不符）")
            cur = cur[seg]
        else:
            if not isinstance(cur, dict):
                raise PathNotFound(f"路径 {walked!r} 无法解析（父级不是对象）")
            real = _match_key(cur, seg)
            if real is None:
                raise PathNotFound(f"路径 {walked!r} 无法解析（字段不存在）")
            cur = cur[real]
    return cur


def set_in(obj: Any, path: str | list[str | int], value: Any) -> None:
    """按路径写回（脱敏替换用）；父级必须已存在，失败抛 PathNotFound。"""
    segments = parse_path(path) if isinstance(path, str) else path
    if not segments:
        raise PathSyntaxError("路径为空")
    parent = resolve(obj, segments[:-1]) if len(segments) > 1 else obj
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(parent, list) or last >= len(parent):
            raise PathNotFound(f"写回失败：数组下标 {last} 越界")
        parent[last] = value
    else:
        if not isinstance(parent, dict):
            raise PathNotFound("写回失败：父级不是对象")
        real = _match_key(parent, last)
        if real is None:
            raise PathNotFound(f"写回失败：字段 {last!r} 不存在")
        parent[real] = value


def iter_strings(obj: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    """深度遍历，产出 (field_path, 字符串值)。

    field_path 使用解析器自己的语法（`a.b[0].c`），保证可直接喂回 `resolve()`。
    """
    if isinstance(obj, str):
        yield prefix, obj
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            yield from iter_strings(v, child)
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            child = f"{prefix}[{i}]" if prefix else f"[{i}]"
            yield from iter_strings(v, child)
