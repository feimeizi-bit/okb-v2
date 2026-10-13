"""engine.redact 单测（I-12 / I-18 / B 复核 P0-1）

用法：python engine/tests/test_redact.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.redact import (  # noqa: E402
    PiiEntry,
    PiiPipeline,
    UnknownPlaceholderError,
    has_persisted_hit,
    load_pii,
    mask_value,
    materialize,
    normalize_v1,
    redact_argv,
)

ENTRIES = [
    PiiEntry(id="PII_PHONE_1", kind="phone", value="13800000001"),
    PiiEntry(id="PII_ID_1", kind="id_card", value="110101199003071234"),
]


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # materialize
    check("物化真实值", materialize("号码是 <PII_PHONE_1>", ENTRIES) == "号码是 13800000001")
    try:
        materialize("<PII_UNKNOWN_1>", ENTRIES)
        failures.append("未声明占位符应当报错")
    except UnknownPlaceholderError:
        pass

    # load_pii 校验
    try:
        load_pii([{"id": "bad", "kind": "phone", "value": "1"}])
        failures.append("非法 pii.id 应当报错")
    except Exception:  # noqa: BLE001
        pass

    # normalize_v1
    norm, idx = normalize_v1("１３８-００００ ０００１")
    check("归一化全角/连字符/空格", norm == "13800000001")
    check("归一化索引映射", len(idx) == len(norm) and idx[0] == 0 and idx[-1] == len("１３８-００００ ０００１") - 1)

    # exact 命中 + 替换 + 命中记录
    pipeline = PiiPipeline(ENTRIES)
    text = "用户的手机号是 13800000001，请记录。"
    redacted, hits = pipeline.process(text, "payload.text", "reply", "ev_r001_000001")
    check("exact 替换", "13800000001" not in redacted and "<PII_PHONE_1>" in redacted)
    check("exact 命中一条", len(hits) == 1 and hits[0]["match_mode"] == "exact")
    check("命中 surface", hits[0]["surface"] == "reply")
    check("命中 span", text[hits[0]["span"]["start"] : hits[0]["span"]["end"]] == "13800000001")
    check("命中 form=plain", hits[0]["form"] == "plain")
    check("context_excerpt 掩码", "13800000001" not in hits[0]["context_excerpt"])

    # normalized：全角 / 空格 / 连字符
    for raw in ("１３８０００００００１", "138 0000 0001", "138-0000-0001"):
        redacted, hits = pipeline.process(f"号码 {raw} 对吗", "payload.text", "reply", "ev_r001_000002")
        check(f"normalized 替换（{raw}）", "<PII_PHONE_1>" in redacted and raw not in redacted)
        check(f"normalized 命中（{raw}）", any(h["match_mode"] == "normalized" for h in hits))

    # masked_by_agent：只记录、不替换
    masked = "138****0001"
    redacted, hits = pipeline.process(f"已保存为 {masked}", "payload.text", "memory", "ev_r001_000003")
    check("masked 不替换", masked in redacted)
    check(
        "masked 命中",
        len(hits) == 1 and hits[0]["match_mode"] == "masked_by_agent" and hits[0]["form"] == "tail4",
    )

    # P0-1 / §4.6（ISS-01 修正）：泄露按证据行 type 判，不按 surface 字面
    _, memory_hits = pipeline.process("13800000001", "payload.value", "memory", "ev_r001_000005")
    check("memory_write 行命中算泄露", has_persisted_hit(memory_hits, "memory_write"))
    check("file_write 行命中算泄露", has_persisted_hit(memory_hits, "file_write"))
    check("快照(memory_read)命中不算泄露", not has_persisted_hit(memory_hits, "memory_read"))
    check("不传 type 不判泄露", not has_persisted_hit(memory_hits))
    _, reply_hits = pipeline.process("13800000001", "payload.text", "reply", "ev_r001_000004")
    check("回复行不算泄露", not has_persisted_hit(reply_hits, "message"))
    check("无命中不算泄露", not has_persisted_hit([], "memory_write"))

    # mask_value
    check("mask_value 形态", mask_value("110101199003071234") == "1101" + "*" * 10 + "1234")

    # argv 掩码
    masked_argv = redact_argv(
        ["openclaw", "onboard", "--deepseek-api-key", "sk-abcdefgh12345678", "--token=sk-zzzzzzzz99999999", "ok"]
    )
    check("argv 空格分隔掩码", masked_argv[3] == "<REDACTED>")
    check("argv = 连写掩码", masked_argv[4] == "--token=<REDACTED>")
    check("argv 普通参数不动", masked_argv[-1] == "ok")

    if failures:
        print(f"❌ redact 测试未通过：{failures}")
        return 1
    print("✅ redact 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
