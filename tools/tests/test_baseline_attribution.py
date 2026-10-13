#!/usr/bin/env python3
"""写入归因四象限的测试：四个分支都要能走到，且它必须拒绝变成关键词评分器。

真实数据上的首个发现（不是构造出来的）：r2 的 4 道基线题在离线 mock 上全部判为
`never_written`——四层评分只会给一个笼统的 omitted/未通过，而这里能说出"记忆面里从来没有过"。
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

import baseline_attribution as B                            # noqa: E402


def plan_with_baseline():
    return dict(samples=[dict(sample_id="bl_001", checks=[
        dict(id="c_before", scope="memory_before", layer="semantic"),
        dict(id="c_after", scope="memory_after", layer="semantic")])])


def evidence(before_n, after_n, writes=0):
    rows = [dict(task_id="agent-bl_001-r1", type="memory_read",
                 payload=dict(phase="before", entries_count=before_n)),
            dict(task_id="agent-bl_001-r1", type="memory_read",
                 payload=dict(phase="after", entries_count=after_n))]
    for i in range(writes):
        rows.append(dict(task_id="agent-bl_001-r1", type="memory_write",
                         payload=dict(key="k%d" % i, value="v")))
    return rows


class QuadrantTests(unittest.TestCase):
    def run_case(self, before_n, after_n, writes):
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "evidence.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in
                        evidence(before_n, after_n, writes)), encoding="utf-8", newline="\n")
            rep = B.analyse(d, plan_with_baseline())
        self.assertEqual(rep["tasks_with_both_snapshots"], 1, rep)
        return rep["rows"][0]["attribution"]

    def test_all_four_quadrants_reachable(self):
        self.assertEqual(self.run_case(0, 1, 1), "write_attributed")
        self.assertEqual(self.run_case(0, 0, 0), "never_written")
        self.assertEqual(self.run_case(2, 3, 1), "residue_plus_write")
        self.assertEqual(self.run_case(2, 0, 0), "residue_cleared")

    def test_quadrant_reads_the_memory_face_not_only_events(self):
        """判定看终态：没有写入事件但记忆面变非空，仍算 write_attributed；
        residue_cleared 专指"任务前有、任务后清空"（旧语义把它和残留混成一类）。"""
        self.assertEqual(self.run_case(0, 2, 0), "write_attributed")       # 无事件但有终态
        self.assertEqual(self.run_case(1, 1, 0), "residue_plus_write")     # 基线残留且仍在
        self.assertEqual(self.run_case(1, 0, 0), "residue_cleared")        # 前有后无
        self.assertEqual(self.run_case(1, 0, 2), "capture_gap")            # 有写入却读到空

    def test_missing_pair_is_counted_not_zeroed(self):
        """只有 after 快照时不得把 before 当 0（那会把残留误判成"从未写入"）。"""
        rows = [dict(task_id="agent-bl_001-r1", type="memory_read",
                     payload=dict(phase="after", entries_count=0))]
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "evidence.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8",
                                              newline="\n")
            rep = B.analyse(d, plan_with_baseline())
        self.assertEqual(rep["tasks_with_both_snapshots"], 0)
        self.assertEqual(rep["tasks_without_pair"], 1)
        self.assertEqual(rep["attribution_counts"], {})

    def test_entries_count_falls_back_to_coverage_scan(self):
        """真实采集里 entries_count 可能缺，但 coverage.entries_scanned 有值。"""
        rows = [dict(task_id="agent-bl_001-r1", type="memory_read",
                     payload=dict(phase="before", coverage=dict(entries_scanned=0))),
                dict(task_id="agent-bl_001-r1", type="memory_read",
                     payload=dict(phase="after", coverage=dict(entries_scanned=1)))]
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "evidence.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                              encoding="utf-8", newline="\n")
            rep = B.analyse(d, plan_with_baseline())
        self.assertEqual(rep["rows"][0]["attribution"], "write_attributed")

    def test_refuses_plan_without_baseline_rows(self):
        """没有 memory_before 检查项就拒绝运行——否则它会慢慢变成通用关键词评分器。"""
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "evidence.jsonl").write_text("", encoding="utf-8", newline="\n")
            with self.assertRaises(SystemExit) as got:
                B.analyse(d, dict(samples=[dict(sample_id="x", checks=[
                    dict(id="c", scope="memory_after", layer="semantic")])]))
        self.assertIn("memory_before", str(got.exception))

    def test_no_text_matching_anywhere(self):
        """静态守卫：本工具不得读题面正文或做正则匹配（那是违禁判据的入口）。"""
        src = (ROOT / "tools" / "baseline_attribution.py").read_text(encoding="utf-8")
        self.assertNotIn("re.compile", src)
        self.assertNotIn("import re", src)
        self.assertNotIn("value_regex", src)
        for token in ("requirement", "script", "turns"):
            self.assertNotIn('["%s"]' % token, src, "归因工具不应接触题目文本字段 " + token)


class HarnessSelfCheckTests(unittest.TestCase):
    """归因反身检测：仪器也要抓"是我们评测环境的问题"，而且不得指认责任方。"""

    def analyse_rows(self, rows, missing_note=True):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "evidence.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8", newline="\n")
            return B.analyse(d, plan_with_baseline())

    def snap(self, phase, n):
        return dict(task_id="agent-bl_001-r1", type="memory_read",
                    payload=dict(phase=phase, entries_count=n))

    def write(self, key="k"):
        return dict(task_id="agent-bl_001-r1", type="memory_write",
                    payload=dict(key=key, value="v"))

    def test_capture_gap_flag_facts(self):
        """写入事件可见、任务后快照却是 0 条目：先怀疑观测面，而不是判智能体忘光。"""""
        rep = self.analyse_rows([self.snap("before", 0), self.write(), self.snap("after", 0)])
        row = rep["rows"][0]
        self.assertIn("capture_gap_suspect", row["flags"])
        self.assertEqual(row["attribution"], "capture_gap",
                         "有写入事件却读到空记忆面时必须标 capture_gap，不得圆成写入成功")
        self.assertEqual(rep["attribution_counts"], {"capture_gap": 1})
        self.assertEqual(rep["flag_counts"]["capture_gap_suspect"], 1)
        q = [x for x in rep["review_queue"] if x["kind"] == "capture_gap_suspect"]
        self.assertEqual(len(q), 1)
        self.assertIn("memory_surface_paths", q[0]["suggested_check"])
        self.assertFalse(q[0]["counts_toward_score"])

    def test_no_flag_when_pipeline_is_consistent(self):
        """正常形态不得产生任何旗标：这台仪器对我们自己也要是干净的。"""
        rep = self.analyse_rows([self.snap("before", 0), self.write(), self.snap("after", 1)])
        self.assertEqual(rep["rows"][0]["flags"], [])
        self.assertEqual(rep["flag_counts"], {})
        self.assertEqual(rep["review_queue_size"], 0)

    def test_residue_flag_is_reported_as_unattributable(self):
        rep = self.analyse_rows([self.snap("before", 2), self.write(), self.snap("after", 3)])
        self.assertIn("unattributable_residue", rep["rows"][0]["flags"])
        q = [x for x in rep["review_queue"] if x["kind"] == "unattributable_residue"]
        self.assertEqual(len(q), 1)
        self.assertIn("reset()", q[0]["suggested_check"])

    def test_missing_pair_goes_to_queue_not_quadrant(self):
        rows = [dict(task_id="agent-bl_001-r1", type="memory_read",
                     payload=dict(phase="after", entries_count=0))]
        rep = self.analyse_rows(rows)
        self.assertEqual(rep["tasks_with_both_snapshots"], 0)
        kinds = {x["kind"] for x in rep["review_queue"]}
        self.assertEqual(kinds, {"missing_snapshot_pair"})
        self.assertTrue(all(not x["counts_toward_score"] for x in rep["review_queue"]))

    def test_flags_never_reclassify_the_quadrant(self):
        """旗标是附加信息：四象限判定与计数不得被旗标改变。"""
        rep = self.analyse_rows([self.snap("before", 0), self.write(), self.snap("after", 1)])
        self.assertEqual(rep["attribution_counts"], {"write_attributed": 1})
        self.assertEqual(rep["rows"][0]["flags"], [], "一致形态不该被旗标污染")

    def test_queue_does_not_blame_agent_or_harness(self):
        """语言守卫：旗标只说"需要人看"，不得写成智能体失败或环境失败的结论。"""
        src = (pathlib.Path(__file__).resolve().parents[1] / "baseline_attribution.py"
               ).read_text(encoding="utf-8")
        for banned in ("agent_failed", "harness_broken", "判定智能体", "结论："):
            self.assertNotIn(banned, src, "队列语言越界: " + banned)
        rep = self.analyse_rows([self.snap("before", 0), self.write(), self.snap("after", 0)])
        for entry in rep["review_queue"]:
            self.assertTrue(entry["suggested_check"].startswith(("核对", "采集")),
                            "队列条目应是待核事项，不是结论")


class CrossRoundAggregationTests(unittest.TestCase):
    """跨轮归因聚合：稳定性读数只有在"轮间没有残留增长"时才可信。"""

    def test_counts_sum_and_shares_recomputed_not_averaged(self):
        agg = B.aggregate_reports([
            dict(status='computed', run_id='r1', attribution_counts={'never_written': 1},
                 flag_counts={}, tasks_with_both_snapshots=3, tasks_without_pair=0,
                 review_queue_size=0),
            dict(status='computed', run_id='r2', attribution_counts={'never_written': 1,
                                                                     'residue_plus_write': 5},
                 flag_counts={'unattributable_residue': 5}, tasks_with_both_snapshots=9,
                 tasks_without_pair=1, review_queue_size=5)])
        self.assertEqual(agg['attribution_counts'], {'never_written': 2, 'residue_plus_write': 5})
        self.assertEqual(agg['tasks_with_both_snapshots'], 12)
        self.assertEqual(agg['tasks_without_pair'], 1)
        self.assertEqual(agg['never_written_share'], round(2 / 12, 4),
                         "比率必须由求和后的分子/分母重算，不能对各轮比率取平均")
        self.assertEqual(agg['unattributable_share'], round(5 / 12, 4))
        self.assertEqual(agg['per_batch_trend'], {'r1': 0, 'r2': 5})

    def test_residue_growth_is_visible(self):
        agg = B.aggregate_reports([
            dict(status='computed', run_id='r1', attribution_counts={'write_attributed': 4},
                 flag_counts={}, tasks_with_both_snapshots=4, tasks_without_pair=0,
                 review_queue_size=0),
            dict(status='computed', run_id='r2', attribution_counts={'residue_cleared': 4},
                 flag_counts={'unattributable_residue': 4}, tasks_with_both_snapshots=4,
                 tasks_without_pair=0, review_queue_size=4)])
        self.assertEqual(agg['per_batch_trend'], {'r1': 0, 'r2': 4})
        self.assertTrue(any('稳定性' in l for l in agg['limits']),
                        "聚合必须自带警示：残留随轮递增时跨轮稳定性读数本身不可信")

    def test_empty_input_is_not_fabricated_as_clean(self):
        agg = B.aggregate_reports([])
        self.assertEqual(agg['status'], 'not_applicable')
        self.assertIsNone(agg['never_written_share'], "n=0 时不得编造 0.0 或 1.0")
        self.assertEqual(agg['batches_total'], 0)

    def test_degraded_batches_counted_but_contribute_nothing(self):
        agg = B.aggregate_reports([
            dict(status='computed', run_id='r1', attribution_counts={'never_written': 1},
                 flag_counts={}, tasks_with_both_snapshots=1, tasks_without_pair=0,
                 review_queue_size=0),
            dict(status='not_applicable', run_id='r3', reason='计划没有 memory_before')])
        self.assertEqual(agg['batches_computed'], 1)
        self.assertEqual(agg['batches_total'], 2)
        self.assertEqual(agg['tasks_with_both_snapshots'], 1)
        tail = [b for b in agg['batches'] if b['run_id'] == 'r3'][0]
        self.assertEqual(tail['status'], 'not_applicable')
        self.assertIn('memory_before', tail['reason'])

    def test_batch_attributions_degrades_instead_of_crashing(self):
        """跨轮工具遇到"计划不含基线族"必须降级，不能把整份 multirun 报告拖崩。"""
        import report_multirun as RM
        import tempfile
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(str(root), ignore_errors=True))
        run = root / 'runs' / 'r1'
        run.mkdir(parents=True)
        (run / 'evidence.jsonl').write_text('', encoding='utf-8', newline='\n')
        plan_no_baseline = {'samples': [{'sample_id': 'x', 'checks': [
            {'id': 'c', 'scope': 'memory_after', 'layer': 'semantic'}]}]}
        pf = root / 'plan.json'
        pf.write_text(__import__('json').dumps(plan_no_baseline), encoding='utf-8')
        out = RM.batch_attributions(root / 'imports.json', pf, [('r1', run)])
        self.assertEqual(out[0]['status'], 'not_applicable')
        agg = RM.aggregate_reports(out)
        self.assertEqual(agg['status'], 'not_applicable')
        # 聚合结果不得伪装成"干净"
        self.assertEqual(agg['attribution_counts'], {})


if __name__ == "__main__":
    unittest.main(exit=False)
