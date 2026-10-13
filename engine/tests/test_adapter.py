"""engine.adapter 记忆解析单测（I-3 / CONTRACT §15 #1（原 §12.1））

用法：python engine/tests/test_adapter.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.adapter import parse_markdown_memory, parse_sectioned_memory  # noqa: E402
from engine.adapters.hermes import _parse_memory_text  # noqa: E402

MTIME = "2026-01-01T00:00:00+08:00"


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # 每日记忆：行首时间戳
    daily = "2026-09-21 11:31 - number to remember: 7412\n2026-09-21 11:32:05 - 带秒条目\n"
    entries = parse_markdown_memory(daily, "memory/2026-09-21.md", MTIME)
    check("每日条目数", len(entries) == 2)
    check("条目时间戳", entries[0].written_at == "2026-09-21T11:31" and entries[0].written_at_source == "entry_timestamp")
    check("条目值去前缀", entries[0].value == "number to remember: 7412")
    check("秒级时间戳", entries[1].written_at == "2026-09-21T11:32:05")

    # USER.md：observed 元数据 → 下一条目
    user_md = (
        "# USER.md - User Model\n\n"
        "## Directives\n\n"
        "<!-- observed: YYYY-MM-DD | status: active -->\n\n"
        "- Prefer ...\n\n"
        "<!-- observed: 2026-09-21 | status: active -->\n\n"
        "- Never recommend foods containing peanuts.\n"
    )
    entries = parse_markdown_memory(user_md, "USER.md", MTIME)
    by_value = {e.value: e for e in entries}
    check("模板占位不产生 observed", by_value.get("Prefer ...") is not None and by_value["Prefer ..."].written_at_source == "file_mtime")
    check("observed 元数据生效", by_value["Never recommend foods containing peanuts."].written_at == "2026-09-21T00:00:00")
    check("observed 来源标记", by_value["Never recommend foods containing peanuts."].written_at_source == "observed_metadata")

    # 无时间信息的普通 bullet → mtime
    plain = "# MEMORY.md\n\n## 偏好\n- 用户喜欢美式咖啡\n"
    entries = parse_markdown_memory(plain, "MEMORY.md", MTIME)
    check("mtime 兜底", entries and entries[0].written_at == MTIME and entries[0].written_at_source == "file_mtime")

    # ---- ISS-13：Hermes `§` 分段格式（真实 USER.md 形态；此前 entries 恒 0） ----
    hermes_user = (
        "对香菜过敏，点餐/外卖备注永远要写\"免香菜\"（no cilantro）。\n"
        "§\n"
        "订座/订票偏好：靠窗、安静的位置。\n"
    )
    entries = parse_sectioned_memory(hermes_user, "USER.md", MTIME)
    check("分段条目数", len(entries) == 2)
    check("分段 key 合成", entries[0].key == entries[0].value[:80] and "对香菜过敏" in entries[0].key)
    check("分段 mtime 来源", entries[1].written_at == MTIME and entries[1].written_at_source == "file_mtime")
    check("分段 source", all(e.source == "USER.md" for e in entries))
    spaced = "a\n  §  \n\n§\nb\n"
    check("分段容空格/空段", len(parse_sectioned_memory(spaced, "USER.md", MTIME)) == 2)
    inline = "价格是 5§6 元，不分段\n"
    check("行内 § 不分段", len(parse_sectioned_memory(inline, "USER.md", MTIME)) == 1)
    check("适配器选择：§ 多条目", len(_parse_memory_text(hermes_user, "USER.md", MTIME)) == 2)
    single = "用户的出门暗号是「探针蓝色雨伞」\n"
    entries = _parse_memory_text(single, "USER.md", MTIME)
    check("适配器选择：单条目无 §（正向对照现场）", len(entries) == 1 and "探针蓝色雨伞" in entries[0].value)
    check("适配器选择：bullet 文本也成条", len(_parse_memory_text("- 一条 bullet\n", "USER.md", MTIME)) == 1)

    if failures:
        print(f"❌ adapter 测试未通过：{failures}")
        return 1
    print("✅ adapter 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
