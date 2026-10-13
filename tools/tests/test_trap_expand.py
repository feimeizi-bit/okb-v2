"""expect.trap 自动展开的单元与构建级测试（CONTRACT §5 实现）。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT)]

import build_paper  # noqa: E402
from trap_expand import covered_by_anchor, expand_traps  # noqa: E402
from validate import validate_samples  # noqa: E402

DEMO_YAML = """suite_version: v0.1
samples:
  - id: trap_demo
    dimension: dynamic_update
    difficulty: L2
    title: trap 展开测试样本
    script:
      - session: s1
        phase: write
        turns: [{text: 我的新地址是徐汇区漕溪北路 200 号，请记住}]
      - session: s2
        phase: probe
        turns: [{text: 我的地址填哪里？}]
    anchors:
      - id: A1
        type: memory_entry
        key: address
        value_regex: 徐汇
        expect: present
        severity: hard
        on_violation: omitted
    expect:
      verdict: remembered
      confusions: [omitted]
      trap: [浦东新区张江路 100 号]
    score_policy: hybrid
"""


def _sample(**overrides) -> dict:
    sample = {
        "id": "trap_unit",
        "dimension": "dynamic_update",
        "difficulty": "L2",
        "script": [{"session": "s1", "phase": "write", "turns": [{"text": "写入"}]},
                   {"session": "s2", "phase": "probe", "turns": [{"text": "探测"}]}],
        "anchors": [{"id": "A1", "type": "memory_entry", "key": "address",
                     "value_regex": "徐汇", "expect": "present", "severity": "hard",
                     "on_violation": "omitted"}],
        "expect": {"verdict": "remembered", "confusions": ["omitted"],
                   "trap": ["浦东新区张江路 100 号"]},
        "score_policy": "hybrid",
    }
    expect_update = overrides.pop("expect", None)
    sample.update(overrides)
    if expect_update is not None:
        sample["expect"] = expect_update
    return sample


class ExpandUnitTests(unittest.TestCase):
    def test_expands_uncovered_trap_with_contract_fields(self):
        sample = _sample()
        added = expand_traps(sample)
        self.assertEqual([a["id"] for a in added], ["A2"], sample["anchors"])
        anchor = added[0]
        self.assertEqual(anchor["type"], "memory_entry")
        self.assertEqual(anchor["key"], "*")
        self.assertEqual(anchor["expect"], "absent")
        self.assertEqual(anchor["severity"], "soft", "记忆层全量扫描 absent 必须降 soft")
        self.assertEqual(anchor["evaluate_at"], "snapshot_after")
        self.assertEqual(anchor["on_violation"], "omitted", "取 confusions[0]")
        import re
        self.assertIsNotNone(re.search(anchor["value_regex"], "浦东新区张江路 100 号"))
        self.assertIn("expect.trap[0]", anchor["desc"])

    def test_on_violation_uses_primary_confusion(self):
        sample = _sample(expect={"verdict": "remembered", "confusions": ["wrongly_reused", "omitted"],
                                 "trap": ["旧值"]})
        added = expand_traps(sample)
        self.assertEqual(added[0]["on_violation"], "wrongly_reused")

    def test_covered_trap_is_not_expanded(self):
        sample = _sample(expect={"verdict": "remembered", "confusions": ["omitted"], "trap": ["徐汇"]})
        self.assertEqual(expand_traps(sample), [], "已被锚点子串覆盖的 trap 不重复展开")

    def test_escaped_regex_coverage_counts(self):
        sample = _sample()
        sample["anchors"] = [dict(sample["anchors"][0], value_regex=r"10\.10\.1\.27")]
        sample["expect"] = {"verdict": "remembered", "confusions": ["omitted"], "trap": ["10.10.1.27"]}
        self.assertTrue(covered_by_anchor("10.10.1.27", sample["anchors"]))
        self.assertEqual(expand_traps(sample), [])

    def test_rule_only_is_untouched(self):
        sample = _sample(score_policy="rule_only")
        self.assertEqual(expand_traps(sample), [], "rule_only 不允许 soft，留给作者/B 编译链")

    def test_idempotent_and_ids_sequence(self):
        sample = _sample(expect={"verdict": "remembered", "confusions": ["omitted"],
                                 "trap": ["旧A", "旧B"]})
        first = expand_traps(sample)
        self.assertEqual([a["id"] for a in first], ["A2", "A3"])
        self.assertEqual(expand_traps(sample), [], "幂等：已展开不重复添加")

    def test_empty_trap_is_noop(self):
        sample = _sample(expect={"verdict": "remembered", "confusions": ["omitted"], "trap": []})
        self.assertEqual(expand_traps(sample), [])


class BuildIntegrationTests(unittest.TestCase):
    def _build(self, expand: bool) -> list[dict]:
        with tempfile.TemporaryDirectory() as td:
            yaml_path = pathlib.Path(td) / "demo.yaml"
            out = pathlib.Path(td) / "paper.jsonl"
            yaml_path.write_text(DEMO_YAML, encoding="utf-8")
            rc = build_paper.build([str(yaml_path)], out, expand_trap_values=expand)
            self.assertEqual(rc, 0, "构建必须成功（展开与否都不允许 error）")
            return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    def test_flag_expands_and_clears_w206(self):
        rows = self._build(expand=True)
        self.assertEqual(len(rows[0]["anchors"]), 2)
        self.assertEqual(rows[0]["anchors"][1]["id"], "A2")
        report = validate_samples(rows)
        self.assertEqual([i for i in report.issues if i.level == "error"], [])
        self.assertNotIn("W206", {i.code for i in report.issues}, "展开后 W206 不应再出现")

    def test_default_is_byte_legacy_no_expansion(self):
        rows = self._build(expand=False)
        self.assertEqual(len(rows[0]["anchors"]), 1, "默认关：不得改变历史卷字节")
        report = validate_samples(rows)
        self.assertIn("W206", {i.code for i in report.issues}, "未展开时 W206 如实保留")


if __name__ == "__main__":
    unittest.main()
