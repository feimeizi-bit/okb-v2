"""自包含审题单生成器测试：嵌数据可解析、判官面零泄漏、导出件格式与 validate 对合。

合成工作单是程序造的假数据，用来验证生成器不会放行泄题/坏格式；
真实审题单只能从 annotate_checks 的公开 worklist.jsonl 产生。
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import make_check_sheet as M                                     # noqa: E402


def synth_worklist(path: pathlib.Path) -> None:
    head = dict(format="okb-check-worklist-v1", state="open", source_package="demo", sampled=2)
    items = [
        dict(row_key="agent-a#c1#abcd1234", item_sha256="x",
             item=dict(requirement="要求含 <script> 与引号 \"q\"", scope="memory_after",
                       task=dict(dimension="dynamic_update",
                                 script=[dict(session="s1", turns=[{"text": "记得 <b> 吗"}])],
                                 initial_files=[dict(path="a.md", content="旧值\n")]),
                       observations=[dict(evidence_id="E1",
                                          content=dict(phase="after", via="probe",
                                                       entries_count=1,
                                                       entries=[dict(key="k", value="v")]))])),
        dict(row_key="agent-b#c2#efgh5678", item_sha256="y",
             item=dict(requirement="第二条", scope="file_final",
                       task=dict(dimension="task_reuse", script=[], initial_files=[]),
                       observations=[dict(evidence_id="E1",
                                          content=dict(path="a.md", op="create",
                                                       before_content=None,
                                                       after_content="已写"))])),
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [head] + items) + "\n",
                    encoding="utf-8", newline="\n")


class MakeCheckSheetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.wl = self.root / "worklist.jsonl"
        synth_worklist(self.wl)

    def build(self):
        return M.build_html(self.wl, "标题", "2026-10-14")

    def test_payload_roundtrip_and_escape(self):
        page, payload = self.build()
        self.assertEqual(payload["format"], M.FORMAT_SHEET)
        self.assertEqual(len(payload["items"]), 2)
        m = re.search(r"const SHEET = (.*?);\nconst draftKey", page, re.S)
        sheet = json.loads(m.group(1))
        self.assertEqual([i["row_key"] for i in sheet["items"]],
                         ["agent-a#c1#abcd1234", "agent-b#c2#efgh5678"])
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script> 与", page)
        self.assertEqual(page.count('<section class="q"'), 2)

    def test_no_judge_side_leak(self):
        page, _ = self.build()
        for bad in ('"judge_result"', '"on_failure"', '"evidence_tier"', '"rationale"',
                    '"citations"'):
            self.assertNotIn(bad, page)

    def test_export_rows_pass_annotate_validate(self):
        _, payload = self.build()
        labels = self.root / "labels-test.jsonl"
        with labels.open("w", encoding="utf-8", newline="\n") as fh:
            for it in payload["items"]:
                fh.write(json.dumps(dict(format="okb-check-labels-v1", row_key=it["row_key"],
                                         label="supported",
                                         note="测试用机器标签，不是人类标签"),
                                    ensure_ascii=False) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "annotate_checks.py"),
                            "validate", "--worklist", str(self.wl), "--labels", str(labels)],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_prefill_checks_radios_and_notes(self):
        labels = self.root / "ai-draft.jsonl"
        rows = [
            dict(format="okb-check-labels-v1", row_key="agent-a#c1#abcd1234",
                 label="contradicted", note="AI 草案理由 <b>注意</b>", source="ai-draft"),
            dict(format="okb-check-labels-v1", row_key="agent-b#c2#efgh5678",
                 label="supported", note="另一条草案理由"),
        ]
        labels.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                          encoding="utf-8", newline="\n")
        page, payload = M.build_html(self.wl, "标题", "2026-10-14", labels)
        self.assertIsNotNone(payload["prefill_sha256"])
        self.assertIn("value='contradicted' checked", page)
        self.assertIn("value='supported' checked", page)
        self.assertIn("AI 草案理由 &lt;b&gt;注意&lt;/b&gt;", page)
        self.assertNotIn("<b>注意</b>", page)
        self.assertIn("ai-tag", page)
        self.assertIn("rev-Q1", page)
        self.assertIn("本页已预填", page)
        self.assertNotIn("__PREFILL_BANNER__", page)

    def test_prefill_rowkey_mismatch_rejected(self):
        labels = self.root / "bad.jsonl"
        labels.write_text(json.dumps(dict(row_key="someone-else", label="supported",
                                          note="note long enough"), ensure_ascii=False) + "\n",
                          encoding="utf-8", newline="\n")
        with self.assertRaises(ValueError):
            M.build_html(self.wl, "t", "2026-10-14", labels)

    def test_main_writes_lf_file(self):
        out = self.root / "sheet.html"
        rc = M.main(["--worklist", str(self.wl), "--out", str(out),
                     "--generated-at", "2026-10-14"])
        self.assertEqual(rc, 0)
        data = out.read_bytes()
        self.assertNotIn(b"\r\n", data)
        self.assertIn(b"<!DOCTYPE html>", data)


if __name__ == "__main__":
    unittest.main()
