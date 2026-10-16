"""engine.paths 单测（CONTRACT §15 #8（原 §12 #8））

用法：python engine/tests/test_paths.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.paths import (  # noqa: E402
    PathNotFound,
    PathSyntaxError,
    iter_strings,
    parse_path,
    resolve,
    set_in,
)


def expect_error(exc_type, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"期望 {exc_type.__name__}，但没有报错")


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # parse_path
    check("parse 基础", parse_path("a.b[0].c") == ["a", "b", 0, "c"])
    check("parse 单段", parse_path("payload") == ["payload"])
    check("parse 连续下标", parse_path("a[0][1]") == ["a", 0, 1])
    expect_error(PathSyntaxError, parse_path, "")
    expect_error(PathSyntaxError, parse_path, "a..b")
    expect_error(PathSyntaxError, parse_path, "a[")
    expect_error(PathSyntaxError, parse_path, "a[x]")

    # resolve：trim + 大小写不敏感
    obj = {"Payload": {"Value": [{"Text": "hi"}]}}
    check("resolve 大小写", resolve(obj, "payload.value[0].text") == "hi")
    check("resolve 带空白键", resolve({"  k  ": 1}, "k") == 1)
    expect_error(PathNotFound, resolve, obj, "payload.missing")
    expect_error(PathNotFound, resolve, obj, "payload.value[3]")

    # set_in 往返
    set_in(obj, "payload.value[0].text", "changed")
    check("set_in 往返", resolve(obj, "payload.value[0].text") == "changed")

    # iter_strings：路径可直接喂回 resolve
    sample = {"a": {"b": ["x", "y"]}, "n": 1, "c": {"d": "z"}}
    pairs = dict(iter_strings(sample, "payload"))
    check("iter 路径数", len(pairs) == 3)
    check("iter 路径可解析", all(resolve({"payload": sample}, p) == v for p, v in pairs.items()))
    check("iter 数组路径", pairs.get("payload.a.b[1]") == "y")

    if failures:
        print(f"❌ paths 测试未通过：{failures}")
        return 1
    print("✅ paths 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
