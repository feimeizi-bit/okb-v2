"""engine.workspace 单测（I-10 / I-14 / CONTRACT §6）

用法：python engine/tests/test_workspace.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine.redact import PiiEntry, PiiPipeline  # noqa: E402
from engine.workspace import PathSafetyError, Workspace, validate_relpath  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # 路径安全
    for bad in ("/abs/path", "a/../b", "a\\b", " a", "a ", "a//b", "a/./b", "C:/x", "", "a\x00b"):
        try:
            validate_relpath(bad)
            failures.append(f"非法路径未拒绝：{bad!r}")
        except PathSafetyError:
            pass
    check("合法路径通过", validate_relpath("dir/file.txt") == "dir/file.txt")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(pathlib.Path(tmp) / "work")
        ws.reset()
        entry = PiiEntry("PII_PHONE_1", "phone", "13800000001")
        created = ws.materialize(
            [
                {"path": "订单.yaml", "content": "id: 1\nphone: <PII_PHONE_1>"},
                {"path": "sub/a.txt", "content": "hello"},
            ],
            PiiPipeline([entry]),
        )
        check("fixture 创建数", len(created) == 2)
        check("fixture 子目录", (ws.root / "sub" / "a.txt").is_file())
        check("fixture 物化", (ws.root / "订单.yaml").read_text(encoding="utf-8") == "id: 1\nphone: 13800000001")

        # F1（stage4 审计）：物化字节跨平台确定——显式 LF，禁止平台默认换行改写
        yaml_bytes = (ws.root / "订单.yaml").read_bytes()
        check("fixture LF 无 CR", b"\r\n" not in yaml_bytes)
        check("fixture 字节=UTF-8(LF)",
              yaml_bytes == "id: 1\nphone: 13800000001".encode("utf-8"))

        # F1 同类：RawStore 文本字节（raw 归档）跨平台确定
        from engine.collectors import RawStore  # noqa: PLC0415

        raw = RawStore(pathlib.Path(tmp) / "run_raw")
        raw_ref = raw.write_text("t1", "s1_t0_stdout.txt", "第一行\n第二行\n")
        raw_bytes = (pathlib.Path(tmp) / "run_raw" / raw_ref["path"]).read_bytes()
        check("raw 文本 LF 无 CR", b"\r\n" not in raw_bytes and raw_bytes.decode("utf-8") == "第一行\n第二行\n")

        # 快照 / diff：create + modify + delete
        before = ws.snapshot()
        (ws.root / "sub" / "a.txt").write_text("hello world", encoding="utf-8")
        (ws.root / "new.txt").write_text("new", encoding="utf-8")
        (ws.root / "订单.yaml").unlink()
        after = ws.snapshot()
        changes = {c.path: c.op for c in ws.diff(before, after)}
        check("diff modify", changes.get("sub/a.txt") == "modify")
        check("diff create", changes.get("new.txt") == "create")
        check("diff delete", changes.get("订单.yaml") == "delete")
        check("listing 数量", len(ws.listing()) == 2)

        # 读取
        check("read_text", ws.read_text("new.txt") == "new")
        (ws.root / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
        check("二进制返回 None", ws.read_text("bin.dat") is None)
        check("不存在返回 None", ws.read_text("nope.txt") is None)

        ws.cleanup()
        check("清理后目录不存在", not ws.root.exists())

        # keep 模式清理（Agent 自有工作区：保留系统文件）
        keep_root = pathlib.Path(tmp) / "work_keep"
        ws_keep = Workspace(keep_root)
        ws_keep.reset()
        (keep_root / "AGENTS.md").write_text("keep", encoding="utf-8")
        (keep_root / "task_file.txt").write_text("drop", encoding="utf-8")
        (keep_root / "subdir").mkdir()
        (keep_root / "subdir" / "x.txt").write_text("drop", encoding="utf-8")
        ws_keep.reset(keep={"AGENTS.md"})
        check("keep 保留系统文件", (keep_root / "AGENTS.md").is_file())
        check("keep 清理任务文件", not (keep_root / "task_file.txt").exists())
        check("keep 清理子目录", not (keep_root / "subdir").exists())
        ws_keep.cleanup(keep={"AGENTS.md"})
        check("keep 模式 cleanup 不删根", keep_root.exists() and (keep_root / "AGENTS.md").is_file())

        # ignore_top：快照跳过 .git 与记忆面
        ign_root = pathlib.Path(tmp) / "work_ignore"
        ws_ign = Workspace(ign_root, ignore_top={".git", "MEMORY.md"})
        ws_ign.reset()
        (ign_root / ".git").mkdir()
        (ign_root / ".git" / "obj").write_text("x", encoding="utf-8")
        (ign_root / "MEMORY.md").write_text("m", encoding="utf-8")
        (ign_root / "a.txt").write_text("a", encoding="utf-8")
        snap = ws_ign.snapshot()
        check("ignore 跳过 .git", not any(p.startswith(".git") for p in snap))
        check("ignore 跳过记忆面", "MEMORY.md" not in snap)
        check("ignore 保留普通文件", "a.txt" in snap)

    if failures:
        print(f"❌ workspace 测试未通过：{failures}")
        return 1
    print("✅ workspace 测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
