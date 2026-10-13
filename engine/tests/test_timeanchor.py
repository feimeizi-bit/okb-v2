"""engine.timeanchor 单测（I-15 方案 2）

用法：python engine/tests/test_timeanchor.py
"""
from __future__ import annotations

import datetime
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.timeanchor import (  # noqa: E402
    normalize_openclaw_memory,
    parse_anchor,
    rewrite_observed_dates,
    set_mtimes,
    shift_entry_timestamps,
)


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # parse_anchor
    aware = parse_anchor("2026-05-01T12:30:00+08:00")
    check("锚点带时区", aware.utcoffset() == datetime.timedelta(hours=8))
    naive = parse_anchor("2026-05-01T12:30:00")
    check("锚点无时区按本机", naive.tzinfo is not None)

    # shift_entry_timestamps
    session_start = datetime.datetime(2026, 9, 21, 11, 31, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    anchor = datetime.datetime(2026, 5, 1, 12, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    delta = anchor - session_start
    text = "2026-09-21 11:31 - number to remember: 7412\n没有时间戳的一行\n2026-09-21 11:32:05 - 带秒的条目\n"
    shifted, count = shift_entry_timestamps(text, delta)
    expected_1 = (datetime.datetime(2026, 9, 21, 11, 31) + delta).strftime("%Y-%m-%d %H:%M")
    expected_2 = (datetime.datetime(2026, 9, 21, 11, 32, 5) + delta).strftime("%Y-%m-%d %H:%M:%S")
    check("平移条目数", count == 2)
    check("分钟级格式", shifted.startswith(expected_1 + " - number to remember: 7412"))
    check("非时间戳行不动", "没有时间戳的一行" in shifted)
    check("秒级格式保留", expected_2 + " - 带秒的条目" in shifted)

    # rewrite_observed_dates
    observed_text = (
        "<!-- observed: 2026-09-21 | status: active -->\n- 新指令\n"
        "<!-- observed: 2026-01-01 | status: active -->\n- 旧指令\n"
        "<!-- observed: YYYY-MM-DD | status: active -->\n- 模板占位\n"
    )
    new_text, n = rewrite_observed_dates(observed_text, "2026-09-21", "2026-05-01")
    check("observed 改写数", n == 1)
    check("observed 改为锚点", "observed: 2026-05-01" in new_text)
    check("其它日期不动", "observed: 2026-01-01" in new_text and "observed: YYYY-MM-DD" in new_text)

    # normalize_openclaw_memory：改名 + 平移
    with tempfile.TemporaryDirectory() as tmp:
        mem = pathlib.Path(tmp) / "memory"
        mem.mkdir()
        (mem / "2026-09-21.md").write_text("2026-09-21 11:31 - 用户喜欢美式咖啡\n", encoding="utf-8")
        result = normalize_openclaw_memory(mem, anchor, session_start)
        check("改名到锚点日期", (mem / "2026-05-01.md").is_file() and not (mem / "2026-09-21.md").exists())
        check("条目平移记录", result["entries_shifted"] == 1)
        content = (mem / "2026-05-01.md").read_text(encoding="utf-8")
        check("内容前缀已平移", content.startswith(expected_1) and "用户喜欢美式咖啡" in content)

        # 合并：同锚点日期再次写入
        (mem / "2026-09-22.md").write_text("2026-09-22 09:00 - 第二条\n", encoding="utf-8")
        result2 = normalize_openclaw_memory(mem, anchor, datetime.datetime(2026, 9, 22, 9, 0, tzinfo=anchor.tzinfo))
        merged = (mem / "2026-05-01.md").read_text(encoding="utf-8")
        check("合并同日期文件", "第二条" in merged and not (mem / "2026-09-22.md").exists())
        check("合并动作记录", any("merge" in a["action"] for a in result2["renamed"]))

        # 同日锚点：原地重写
        same_day = anchor
        (mem / "2026-05-01.md").write_text("2026-05-01 10:00 - 原地\n", encoding="utf-8")
        result3 = normalize_openclaw_memory(mem, same_day, datetime.datetime(2026, 5, 1, 9, 0, tzinfo=anchor.tzinfo))
        check("同日原地重写", any(a["action"] == "rewrite" for a in result3["renamed"]))

        # set_mtimes
        ws = pathlib.Path(tmp) / "ws"
        ws.mkdir()
        (ws / "a.txt").write_text("x", encoding="utf-8")
        n = set_mtimes(ws, ["a.txt", "missing.txt"], anchor)
        check("mtime 处理数", n == 1)
        mtime = datetime.datetime.fromtimestamp((ws / "a.txt").stat().st_mtime).astimezone()
        check("mtime 等于锚点", abs((mtime - anchor).total_seconds()) < 1)

    if failures:
        print(f"❌ timeanchor 测试未通过：{failures}")
        return 1
    print("✅ timeanchor 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
