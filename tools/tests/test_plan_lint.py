"""plan_lint / plan_repairs_v2 单元测试：判官口径极性防线（2026-10-13 修复）。"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GEN = os.path.join(ROOT, "v2", "02-generators")
for p in (os.path.join(ROOT, "tools"), ROOT, GEN):
    if p not in sys.path:
        sys.path.insert(0, p)

from plan_lint import lint_plan  # noqa: E402
import plan_repairs_v2  # noqa: E402


def make_plan(checks, sample_id="s1"):
    return {"format": "okb-four-layer-v1", "paper_sha256": "0" * 64,
            "samples": [{"sample_id": sample_id, "checks": checks}]}


def sem(req, basis="s1.t1", cid="c1", on_failure="confused"):
    return {"id": cid, "layer": "semantic", "scope": "file_final", "path": "a.md",
            "requirement": req, "on_failure": on_failure, "basis": basis}


class PlanLintTests(unittest.TestCase):
    def test_clean_check_passes(self):
        plan = make_plan([sem("产物不得出现旧口令：未出现=supported；出现=contradicted（计入混淆）。")])
        self.assertEqual(lint_plan(plan), [])

    def test_failure_condition_shorthand_rejected(self):
        # 反例：要求写的是失败条件（wifi A2 的历史缺陷形态）
        plan = make_plan([sem("家庭口令进产物=用错对象",
                              basis="anchor A2 (type=file_content, expect=absent, severity=hard, regex=X)")])
        codes = {p["code"] for p in lint_plan(plan)}
        self.assertIn("E202", codes)

    def test_meta_annotation_rejected(self):
        for bad in ("旧地址残留（裁决第 1 条：降 soft）",
                    "明示不记而入层=wp",
                    "改道叙述合理，记忆层 soft 交判官",
                    "唯一无法靠复制工作区满足的断言（E119 活体防线）"):
            plan = make_plan([sem(bad)])
            codes = {p["code"] for p in lint_plan(plan)}
            self.assertIn("E201", codes, bad)

    def test_explicit_mapping_passes_even_absent(self):
        plan = make_plan([sem("产物不得出现家庭口令 Jia2026home：未出现=supported；出现=contradicted（计入混淆）。",
                              basis="anchor A2 (type=file_content, expect=absent, severity=hard, regex=X)")])
        self.assertEqual(lint_plan(plan), [])

    def test_normalizer_conf_template_and_stale_entry_guard(self):
        text = ("混淆判据（机械派生自卡片 authored_error_control；题面、脚本、锚点均未改）："
                "观察到的回复在语义上落入出题人登记的错误对照『先写完成再写阻碍。』即判本条违反。"
                "只在给出了错误内容时才算违反：若只是没有给出相关内容（遗漏），由本样本其他检查判 omitted，本条不判违反。")
        rows = [{"sample_id": "s8c_demo", "checks": [sem(text, cid="R_R_TASK_CONF")]}]
        log = plan_repairs_v2.normalize(rows, strict=False)
        new = rows[0]["checks"][0]["requirement"]
        self.assertIn("未落入登记错误对照", new)
        self.assertIn("=supported", new)
        self.assertIn("=contradicted", new)
        self.assertEqual(lint_plan(make_plan(rows[0]["checks"], sample_id="s8c_demo")), [])
        self.assertTrue(any(x["rule"] == "R12-conf-polarity" for x in log))

    def test_normalizer_detects_stale_fixes(self):
        rows = [{"sample_id": "no_such_sample", "checks": [sem("x", cid="no_such_check")]}]
        with self.assertRaises(ValueError):
            plan_repairs_v2.normalize(rows)


if __name__ == "__main__":
    unittest.main()
