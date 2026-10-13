#!/usr/bin/env python3
"""报告层的归因一节必须是**纯诊断**：加它不得改变任何分数，缺它必须如实说明原因。

为什么单独测：归因读数进报告的最大风险不是"算错"，而是"被当成第七个能力分"。
所以这里核心断言是「带 attribution 与不带 attribution 的报告，在 dimensions/tasks/
overall_score/release_authorized 上逐项完全相同」，其次才是渲染文案与转义。
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

from report_four_layer import main                                    # noqa: E402
from scoring.report_v2 import build_report, render_html               # noqa: E402
from test_report_vnext import ReportTests                             # noqa: E402

FAKE = dict(status="computed", baseline_samples=4, tasks_with_both_snapshots=4,
            tasks_without_pair=0, attribution_counts={"never_written": 4},
            attribution_rates={"never_written": 1.0}, never_written_share=1.0,
            unattributable_share=0.0, rows=[dict(task_id="a-x", sample_id="x",
                                                 attribution="never_written")],
            limits=["诊断量，不参与评分"])


class AttributionSectionTests(ReportTests):
    """复用 test_report_vnext 的合成评分包（两个 Agent、一份四层计划）。"""

    def test_absent_attribution_states_why(self):
        rep = build_report(self.load())
        att = rep["attribution"]
        self.assertEqual(att["status"], "not_computed")
        self.assertIn("memory_before", att["reason"], "缺归因时必须说明缺什么")

    def test_attribution_changes_no_score_anywhere(self):
        base = build_report(self.load())
        withatt = build_report(self.load(), attribution=dict(FAKE))
        for key in ("dimensions", "tasks", "evidence_profile", "overall_score",
                    "release_authorized", "ranking", "review_tasks", "plan_sha256"):
            self.assertEqual(base[key], withatt[key], "归因改变了评分字段 " + key)
        self.assertIsNone(withatt["overall_score"], "诊断量进了报告也不许产生总分")
        self.assertFalse(withatt["release_authorized"])
        self.assertEqual(withatt["attribution"]["never_written_share"], 1.0)

    def test_html_labels_it_diagnostic_and_escaping_holds(self):
        html = render_html(build_report(self.load(), attribution=dict(FAKE)))
        self.assertIn("写入归因", html)
        self.assertIn("不计入六维与排名", html)
        self.assertNotIn("<script", html)
        hostile = dict(FAKE)
        hostile["rows"] = [dict(task_id="</pre><script>alert(1)</script>", attribution="x")]
        html2 = render_html(build_report(self.load(), attribution=hostile))
        self.assertNotIn("<script>alert(1)", html2, "归因行必须转义（内容是外部可控文本）")

    def test_html_without_attribution_still_explains(self):
        html = render_html(build_report(self.load()))
        self.assertIn("not_computed", html)

    def test_cli_marks_not_applicable_when_plan_has_no_baseline_rows(self):
        """fixture 计划只有 memory_after：报告必须照常出，归因标注 not_applicable。"""
        out = self.root / "out-report"
        rc = main(["--score-dir", str(self.score), "--run-dir", str(self.run),
                   "--paper", str(self.paper), "--plan", str(self.plan),
                   "--out-dir", str(out)])
        self.assertEqual(rc, 0, "缺基线族不得让报告失败")
        rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["attribution"]["status"], "not_applicable")
        self.assertIn("memory_before", rep["attribution"]["reason"])


if __name__ == "__main__":
    unittest.main(exit=False)
