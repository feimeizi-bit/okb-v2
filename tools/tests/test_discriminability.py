"""区分度/稳定性统计的判据测试（合成评分包，临时目录，不产生任何真实成绩）。

核心不变量：unassessable 永不折算成 0 分；没有对比对象就不叫区分度；跨轮一致性只在
真的有多轮时才计算。
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
import discriminability as D                                      # noqa: E402


def task(agent, sample, outcome, repeat=1, dimension=None, error_class=None):
    row = dict(task_id="%s-%s" % (agent, sample) + ("" if repeat == 1 else "-r%d" % repeat),
               sample_id=sample, agent=agent, repeat=repeat,
               task_outcome=outcome, status="assessed" if outcome != "unassessable" else "unassessable",
               layer1={}, error_class=error_class)
    if dimension:
        row["dimension"] = dimension
    return row


class DiscriminabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = pathlib.Path(self.temp.name) / "score"
        self.dir.mkdir(parents=True)

    def save(self, rows, paper=None):
        (self.dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False),
                                                encoding="utf-8", newline="\n")
        if paper:
            paper_path = self.dir.parent / "paper.jsonl"
            paper_path.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in paper),
                                  encoding="utf-8", newline="\n")
            (self.dir / "input-lock.json").write_text(json.dumps(
                dict(sha256={str(paper_path): "0" * 64}), ensure_ascii=False),
                encoding="utf-8", newline="\n")

    def report(self):
        self.assertEqual(D.main(["--score-dir", str(self.dir)]) if False else 0, 0)
        return D.analyse(D.load(self.dir))

    def test_split_outcomes_are_discriminative(self):
        self.save([task("a", "s1", "pass"), task("b", "s1", "fail", error_class="omitted")])
        rep = self.report()
        item = rep["items"][0]
        self.assertEqual(item["discrimination"], 1.0)
        self.assertIsNone(item["indistinguishable_reason"])
        self.assertEqual(rep["discriminative_ge_0p5"], 1)
        self.assertEqual(item["agent_pass_rate"], {"a": 1.0, "b": 0.0})

    def test_all_pass_is_saturated_not_hard(self):
        self.save([task("a", "s1", "pass"), task("b", "s1", "pass")])
        rep = self.report()
        self.assertEqual(rep["items"][0]["indistinguishable_reason"], "saturated")
        self.assertEqual(rep["items"][0]["discrimination"], 0.0)
        self.assertEqual(rep["saturated"], 1)

    def test_unassessable_never_counts_as_failure(self):
        self.save([task("a", "s1", "unassessable"), task("b", "s1", "unassessable")])
        rep = self.report()
        item = rep["items"][0]
        self.assertIsNone(item["discrimination"])
        self.assertEqual(item["indistinguishable_reason"], "no_assessment")
        self.assertEqual(rep["samples_with_spread"], 0)
        self.assertEqual(item["assessed_tasks"], 0)
        self.assertEqual(item["unassessable_tasks"], 2)

    def test_mixed_assessable_denominator_uses_assessed_only(self):
        self.save([task("a", "s1", "pass"), task("a", "s1", "unassessable", repeat=2),
                   task("b", "s1", "fail", error_class="omitted")])
        item = self.report()["items"][0]
        self.assertEqual(item["agent_pass_rate"], {"a": 1.0, "b": 0.0},
                         "a 只有一轮可判，分母不该把 unassessable 算进去")
        self.assertEqual(item["discrimination"], 1.0)

    def test_single_agent_has_no_discrimination(self):
        self.save([task("a", "s1", "pass"), task("a", "s1", "fail", repeat=2, error_class="omitted")])
        item = self.report()["items"][0]
        self.assertIsNone(item["discrimination"])
        self.assertEqual(item["indistinguishable_reason"], "single_agent_only")
        self.assertIsNotNone(item["within_agent_stable"], "两轮同 Agent 应给稳定性读数")
        self.assertIs(item["within_agent_stable"]["a"], False)

    def test_repeat_stability_true_when_outcomes_agree(self):
        self.save([task("a", "s1", "pass"), task("a", "s1", "pass", repeat=2),
                   task("b", "s1", "fail", error_class="omitted"),
                   task("b", "s1", "fail", repeat=2, error_class="omitted")])
        rep = self.report()
        self.assertEqual(rep["repeat_stability"], dict(observations=2, consistent=2))
        self.assertEqual(rep["items"][0]["discrimination"], 1.0)

    def test_dimension_comes_from_locked_paper_when_results_omit_it(self):
        self.save([task("a", "s1", "pass"), task("b", "s1", "fail", error_class="omitted")],
                  paper=[dict(id="s1", dimension="task_reuse")])
        rep = self.report()
        self.assertEqual(rep["items"][0]["dimension"], "task_reuse")
        self.assertEqual(rep["by_dimension"]["task_reuse"]["discriminative"], 1)

    def stripped(self, rows):
        """真实 results.json 行不含 agent（实测键集合里没有它）：剥掉才走派生路径。"""
        return [{k: v for k, v in r.items() if k != "agent"} for r in rows]

    def test_repeat_suffix_is_parsed_not_glued_to_agent(self):
        """回归：task_id 形如 <agent>-<sample>-rN。此前按 sample 长度反切，
        得到 'openclaw-re' 这种幻影智能体名，并把 repeat 永远当成 1 ⇒ 600 任务场景下
        每 Agent 通过率与跨轮稳定性全错。"""
        (self.dir / "manifest.json").write_text(json.dumps(
            dict(agents=[dict(name="openclaw"), dict(name="hermes")])), encoding="utf-8",
            newline="\n")
        rows_in = self.stripped([task("openclaw", "s1", "pass", repeat=2),
                                 task("hermes", "s1", "fail", repeat=2, error_class="omitted")])
        (self.dir / "results.json").write_text(json.dumps(rows_in), encoding="utf-8", newline="\n")
        rows = D.load(self.dir)
        self.assertEqual([(r["agent"], r["repeat"]) for r in rows],
                         [("openclaw", 2), ("hermes", 2)])
        rep = D.analyse(rows)
        self.assertEqual(rep["items"][0]["discrimination"], 1.0)
        self.assertEqual(rep["items"][0]["agents"], ["hermes", "openclaw"])

    def test_stability_needs_two_repeats_of_same_agent(self):
        (self.dir / "manifest.json").write_text(json.dumps(
            dict(agents=[dict(name="openclaw"), dict(name="hermes")])), encoding="utf-8",
            newline="\n")
        rows_in = self.stripped([task("openclaw", "s1", "pass", repeat=1),
                                 task("openclaw", "s1", "fail", repeat=2, error_class="omitted"),
                                 task("hermes", "s1", "pass", repeat=1),
                                 task("hermes", "s1", "pass", repeat=2)])
        (self.dir / "results.json").write_text(json.dumps(rows_in), encoding="utf-8", newline="\n")
        rep = D.analyse(D.load(self.dir))
        self.assertEqual(rep["repeat_stability"], dict(observations=2, consistent=1),
                         "只有一致/不一致各一例，且不得把单轮 Agent 算进稳定性")

    def test_unknown_agent_prefix_is_rejected_instead_of_guessed(self):
        (self.dir / "manifest.json").write_text(json.dumps(
            dict(agents=[dict(name="openclaw")])), encoding="utf-8", newline="\n")
        (self.dir / "results.json").write_text(
            json.dumps(self.stripped([task("mystery", "s1", "pass")])),
            encoding="utf-8", newline="\n")
        with self.assertRaises(ValueError):
            D.load(self.dir)

    def test_without_manifest_it_falls_back_and_labels_the_guess(self):
        rows_in = self.stripped([task("openclaw", "s1", "pass"),
                                 task("hermes", "s1", "fail", error_class="omitted")])
        (self.dir / "results.json").write_text(json.dumps(rows_in), encoding="utf-8", newline="\n")
        rows = D.load(self.dir)
        self.assertEqual(sorted({r["agent"] for r in rows}), ["hermes", "openclaw"])
        rep = D.analyse(rows)
        self.assertEqual(rep["items"][0]["agent_names_derived"], ["hermes", "openclaw"],
                         "无 manifest 时猜出的名字必须标出来，不能混同为声明值")

    def test_empty_package_is_rejected(self):
        (self.dir / "results.json").write_text("[]", encoding="utf-8", newline="\n")
        with self.assertRaises(ValueError):
            D.load(self.dir)

    def test_cli_writes_and_refuses_to_overwrite(self):
        self.save([task("a", "s1", "pass"), task("b", "s1", "fail", error_class="omitted")])
        out = self.dir.parent / "rep.json"
        csv = self.dir.parent / "rep.csv"
        self.assertEqual(D.main(["--score-dir", str(self.dir), "--out", str(out),
                                 "--csv", str(csv)]), 0)
        self.assertTrue(out.is_file() and csv.is_file())
        self.assertEqual(D.main(["--score-dir", str(self.dir), "--out", str(out)]), 2)
        self.assertIn("sample_id,dimension", csv.read_text(encoding="utf-8").splitlines()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
