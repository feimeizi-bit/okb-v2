#!/usr/bin/env python3
"""第二条人审回路：让人类解释**仪器自己说不清的行**，并量化"人工 vs 旗标"的一致率。

它与第一条回路（人审判据）的区别必须守住：
  · 题面只给观测事实（前后快照条目数、写入事件），**不给**我们的象限/旗标/建议核查项；
  · 候选答案是"解释"而不是"判决"，所以不要求人也参加评分；
  · 我们自己的主张（instrument_label）封在私有文件里，双方交卷前不参与计算；
  · 一切正常（write_attributed）的行不入池——否则一致率会被平凡行灌水。
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

import annotate_checks as A                            # noqa: E402

REPORT = dict(
    format="okb-baseline-attribution-v1",
    rows=[dict(task_id="a-task-001", sample_id="neutral-001", before_entries=0, after_entries=3,
               writes=1, write_keys=["k"], attribution="write_attributed", flags=[]),
          dict(task_id="a-task-002", sample_id="neutral-002", before_entries=0, after_entries=0,
               writes=2, write_keys=["allergy", "order"], attribution="capture_gap",
               flags=["capture_gap_suspect"]),
          dict(task_id="a-task-003", sample_id="neutral-003", before_entries=4, after_entries=5,
               writes=1, write_keys=["k"], attribution="residue_plus_write",
               flags=["unattributable_residue"]),
          dict(task_id="a-task-004", sample_id="neutral-004", before_entries=0, after_entries=0,
               writes=0, write_keys=[], attribution="never_written", flags=[])],
    attribution_counts={"capture_gap": 1, "residue_plus_write": 1, "never_written": 1},
    limits=["诊断量"])


class HarnessReviewLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.report = self.root / "attribution.json"
        self.report.write_text(json.dumps(REPORT, ensure_ascii=False), encoding="utf-8",
                               newline="\n")
        self.wl = self.root / "wl"
        rc = A.main(["make-harness-worklist", "--attribution", str(self.report),
                     "--out-dir", str(self.wl)])
        self.assertEqual(rc, 0)
        self.worklist = self.wl / "worklist.jsonl"

    def lines(self):
        return self.worklist.read_text(encoding="utf-8").splitlines()

    def test_only_claimed_rows_enter_the_pool(self):
        head = json.loads(self.lines()[0])
        self.assertEqual(head["items"], 3, "write_attributed 那种平凡行不该进池灌水")
        ids = [json.loads(l)["item"]["task_id"] for l in self.lines()[1:]]
        self.assertNotIn("a-task-001", ids)

    def test_item_rows_leak_no_instrument_conclusion(self):
        """按**结构**判定失明，而不是对整行做子串匹配：
        样本 id 本身可能带 residue/never_written 之类字样（我第一版就被这种命名假红过），
        真正不能出现的是结论字段名与结论值。"""
        body = self.lines()[1:]
        for line in body:
            row = json.loads(line)
            self.assertEqual(sorted(row), ["item", "item_sha256", "row_key"])
            self.assertEqual(sorted(row["item"]), ["observation", "sample_id", "task_id"])
            obs = row["item"]["observation"]
            self.assertLessEqual(set(obs),
                                 {"before_entries", "after_entries", "write_count", "write_keys",
                                  "write_events", "before_coverage", "after_coverage"})
            for bad in ("attribution", "flags", "suggested_check", "counts_toward_score",
                        "instrument_label", "kind"):
                self.assertNotIn(bad, row)
                self.assertNotIn(bad, row["item"])
                self.assertNotIn(bad, obs)

    def test_options_are_explanations_not_verdicts(self):
        head = json.loads(self.lines()[0])
        self.assertEqual(head["state"], "open")
        self.assertEqual(sorted(head["label_values"]), sorted(A.HARNESS_LABELS))
        self.assertEqual(head["options_are"], "explanations_of_the_observation_not_verdicts")

    def labels_for(self, mapping):
        rows = [json.loads(l) for l in self.lines()[1:]]
        text = json.dumps(dict(format=A.HARNESS_WORKLIST, state="closed",
                               label_values=list(A.HARNESS_LABELS)), ensure_ascii=False) + "\n"
        for r in rows:
            lab, note = mapping.get(r["row_key"], ("", ""))
            text += json.dumps(dict(row_key=r["row_key"], label=lab, note=note),
                               ensure_ascii=False) + "\n"
        self.seq = getattr(self, "seq", 0) + 1
        p = self.root / ("labels-%d.jsonl" % self.seq)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    def test_agreement_refused_before_both_closed(self):
        a = self.labels_for({})
        b = self.labels_for({})
        rc = A.main(["harness-agreement", "--a", str(a), "--b", str(b)])
        self.assertEqual(rc, 2, "未交卷（state=open）不得提前算一致率")

    def test_human_consensus_vs_instrument_agreement(self):
        rows = [json.loads(l) for l in self.lines()[1:]]
        keys = {r["item"]["task_id"]: r["row_key"] for r in rows}
        want = {keys["a-task-002"]: "harness_capture_gap",
                keys["a-task-003"]: "other_task_residue",
                keys["a-task-004"]: "agent_never_wrote"}
        a = self.labels_for({k: (v, "观测与解释一致") for k, v in want.items()})
        b = self.labels_for({k: (v, "同样判断") for k, v in want.items()})
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = A.main(["harness-agreement", "--a", str(a), "--b", str(b),
                         "--flags", str(self.wl / "flags.private.json")])
        self.assertEqual(rc, 0, buf.getvalue())
        out = json.loads(buf.getvalue())
        self.assertEqual(out["items"], 3)
        self.assertEqual(out["human_human"]["cohens_kappa"], 1.0)
        hv = out["human_consensus_vs_instrument"]
        self.assertEqual([hv["comparable"], hv["agreed"]], [3, 3], "三条主张都该被人工复述一致")
        self.assertEqual(hv["agreement_rate"], 1.0)

    def test_disagreement_between_humans_and_instrument_is_visible(self):
        rows = [json.loads(l) for l in self.lines()[1:]]
        keys = {r["item"]["task_id"]: r["row_key"] for r in rows}
        a = self.labels_for({keys["a-task-002"]: ("agent_never_wrote", "我认为它没写"),
                             keys["a-task-003"]: ("other_task_residue", "残留"),
                             keys["a-task-004"]: ("agent_never_wrote", "没写")})
        b = self.labels_for({keys["a-task-002"]: ("agent_never_wrote", "没写"),
                             keys["a-task-003"]: ("other_task_residue", "残留"),
                             keys["a-task-004"]: ("agent_never_wrote", "没写")})
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            A.main(["harness-agreement", "--a", str(a), "--b", str(b),
                    "--flags", str(self.wl / "flags.private.json")])
        out = json.loads(buf.getvalue())
        hv = out["human_consensus_vs_instrument"]
        self.assertEqual([hv["comparable"], hv["agreed"]], [3, 2],
                         "capture_gap 那行人与仪器不一致，必须被计出来而不是消失")
        self.assertEqual(out["human_human"]["cohens_kappa"], 1.0, "两人之间仍然完全一致")

    def test_insufficient_by_both_humans_is_not_an_answer(self):
        rows = [json.loads(l) for l in self.lines()[1:]]
        keys = {r["item"]["task_id"]: r["row_key"] for r in rows}
        a = self.labels_for({k: ("insufficient", "证据不够") for k in keys.values()})
        b = self.labels_for({k: ("insufficient", "看不清") for k in keys.values()})
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            A.main(["harness-agreement", "--a", str(a), "--b", str(b),
                    "--flags", str(self.wl / "flags.private.json")])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["human_consensus_vs_instrument"]["comparable"], 0,
                         "两人都说不足时不得算成'与仪器一致'")
        self.assertEqual(out["disagreements"], [])

    def test_missing_rationale_is_rejected(self):
        rows = [json.loads(l) for l in self.lines()[1:]]
        keys = {r["item"]["task_id"]: r["row_key"] for r in rows}
        a = self.labels_for({k: ("agent_never_wrote", "") for k in keys.values()})
        b = self.labels_for({k: ("agent_never_wrote", "有理由") for k in keys.values()})
        self.assertEqual(A.main(["harness-agreement", "--a", str(a), "--b", str(b)]), 2,
                         "无理由的标签不进统计")


class WithEvidenceDirTests(unittest.TestCase):
    """--run-dir 分支：题面附上写入事件与两侧覆盖范围（人判"观测面漏扫"全靠这些）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.report = self.root / "attribution.json"
        self.report.write_text(json.dumps(REPORT, ensure_ascii=False), encoding="utf-8",
                               newline="\n")
        run = self.root / "runs" / "r1"
        run.mkdir(parents=True)
        ev = [
            dict(task_id="a-task-002", type="memory_read",
                 payload=dict(phase="before", entries_count=0,
                              coverage=dict(files_scanned=1, sources=["MEMORY.md"]))),
            dict(task_id="a-task-002", type="memory_write",
                 payload=dict(key="allergy", value="用户对花生过敏" + ("很长" * 300),
                              via="memory_api", source="MEMORY.md")),
            dict(task_id="a-task-002", type="memory_write",
                 payload=dict(key="order", value="先核对依赖", via="memory_api", source="x")),
            dict(task_id="a-task-002", type="memory_read",
                 payload=dict(phase="after", entries_count=0,
                              coverage=dict(files_scanned=1, sources=["MEMORY.md"]))),
        ]
        (run / "evidence.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ev),
            encoding="utf-8", newline="\n")
        self.run, self.wl = run, self.root / "wl"
        rc = A.main(["make-harness-worklist", "--attribution", str(self.report),
                     "--run-dir", str(run), "--out-dir", str(self.wl)])
        self.assertEqual(rc, 0)

    def body(self):
        return [json.loads(l) for l in self.worklist_lines()[1:]]

    def worklist_lines(self):
        return (self.wl / "worklist.jsonl").read_text(encoding="utf-8").splitlines()

    def test_observation_carries_events_and_coverage(self):
        rows = {r["item"]["task_id"]: r["item"]["observation"] for r in self.body()}
        obs = rows["a-task-002"]
        self.assertEqual(len(obs["write_events"]), 2)
        self.assertEqual(obs["write_events"][1]["value"], "先核对依赖")
        self.assertTrue(obs["write_events"][0]["truncated"], "超长值必须截断并标注")
        self.assertLessEqual(len(obs["write_events"][0]["value"]), A.MAX_VALUE_CHARS)
        self.assertEqual(obs["before_coverage"]["coverage"]["sources"], ["MEMORY.md"])
        self.assertEqual(obs["after_coverage"]["entries_count"], 0)

    def test_still_no_conclusion_fields_with_evidence(self):
        for r in self.body():
            obs = r["item"]["observation"]
            self.assertNotIn("attribution", obs)
            self.assertNotIn("flags", obs)
            blob = json.dumps(r, ensure_ascii=False)
            for bad in ("suggested_check", "counts_toward_score", "instrument_label"):
                self.assertNotIn(bad, blob)

    def test_missing_evidence_file_is_refused_not_silently_skipped(self):
        rc = A.main(["make-harness-worklist", "--attribution", str(self.report),
                     "--run-dir", str(self.root / "runs" / "nope"), "--out-dir",
                     str(self.root / "wl2")])
        self.assertEqual(rc, 2, "指定了 --run-dir 却读不到证据，必须拒绝而不是静默少给信息")


if __name__ == "__main__":
    unittest.main(exit=False)
