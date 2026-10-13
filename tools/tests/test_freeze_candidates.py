"""paper-r1 编译器自检：闸、原因码、禁止编造、并入逐字节一致、复算确定性。

全部离线：只读私有候选卡包与仓内 pilot 样本，输出写进临时目录，不覆盖已提交产物。
候选卡包不存在时整个套件 skip（公开口径下私有材料可以缺席）。
"""
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
import freeze_candidates as F                                    # noqa: E402
from scoring.four_layer import validate_plan                      # noqa: E402

CARDS = pathlib.Path(r"D:/a_shanghai/_review/four-layer/s8-batch18-candidate-design"
                     "/cards-r3/candidate-cards.json")


# 出货配置：结构锚点开、三相枚举限制下的 update 卡拒收、放开时间/重启闸、并入 batchA、凑 100 题
SHIPPED = ["--extra-suite", str(ROOT / "samples/v1.0-batchA.yaml"), "--allow-restart",
           "--include-long-wait", "--allow-time-mode", "cross_session_only",
           "--allow-time-mode", "actual_elapsed",
           "--allow-time-mode", "logical_calendar_reference", "--target-samples", "100"]


def run(args_extra, out_dir, shipped=True):
    argv = ["--cards", str(CARDS), "--pilot", str(ROOT / "samples/pilot-v0.2.yaml"),
            "--baseline-plan", str(ROOT / "samples/four-layer-pilot.v1.json"),
            "--out-paper", str(out_dir / "paper.yaml"), "--out-plan", str(out_dir / "plan.json"),
            "--out-jsonl", str(out_dir / "paper.jsonl"),
            "--out-matrix", str(out_dir / "matrix.json")] + (SHIPPED if shipped else []) \
        + list(args_extra)
    return F.main(argv)


def last_json(text):
    """工具打印的是缩进 JSON 块，且 stdout 里还有 build_paper 的行；取最后一个完整对象。"""
    lines = text.splitlines()
    end = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "}":
            end = i
            break
        if lines[i].strip().startswith("{"):
            return json.loads(lines[i])
    if end is None:
        raise AssertionError("no JSON object in captured stdout:\n" + text[-800:])
    start = max(i for i in range(end + 1) if lines[i].strip() == "{")
    return json.loads("\n".join(lines[start:end + 1]))


@unittest.skipUnless(CARDS.is_file(), "private candidate card package not present")
class FreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temp.name)
        cls.out = cls.root / "a"
        cls.out.mkdir(parents=True)
        for name in ("stdout", "stderr"):
            pass
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            cls.rc = run([], cls.out)
        cls.summary = last_json(buf.getvalue())
        cls.matrix = json.loads((cls.out / "matrix.json").read_text(encoding="utf-8"))
        cls.plan = json.loads((cls.out / "plan.json").read_text(encoding="utf-8"))
        cls.paper_yaml = (cls.out / "paper.yaml").read_text(encoding="utf-8")
        cls.paper = [json.loads(l) for l in
                     (cls.out / "paper.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_compile_exit_zero_and_artifacts_exist(self):
        self.assertEqual(self.rc, 0)
        for name in ("paper.yaml", "plan.json", "paper.jsonl", "matrix.json"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_plan_is_bound_to_compiled_paper_sha(self):
        sha = hashlib.sha256((self.out / "paper.jsonl").read_bytes()).hexdigest()
        self.assertEqual(self.plan["paper_sha256"], sha)
        validate_plan(self.plan, self.paper, sha)          # 不抛即通过

    def test_no_keyword_regex_fabricated_for_card_samples(self):
        """卡片锚点只允许三类：path-only 的 file_content、以及两种结构通配（.* / .+）。

        任何"具体关键词正则/键名"都必须来自卡片原文，否则就是本工具在编判据。
        """
        compiled_ids = {s["id"] for s in self.paper if s["id"].startswith("s8c_")}
        card_text = {c["id"]: json.dumps(c, ensure_ascii=False) for c in
                     json.loads(CARDS.read_text(encoding="utf-8"))["cards"]}
        seen = 0
        for s in self.paper:
            if s["id"] not in compiled_ids:
                continue
            for a in s["anchors"]:
                if a["type"] == "file_content":
                    self.assertIn("path", a)
                    self.assertNotIn("content_regex", a, "content regex would be invented")
                elif a["type"] == "memory_entry":
                    self.assertEqual(a.get("key_regex"), ".*", "memory 锚点只能是结构通配")
                    self.assertNotIn("value_regex", a)
                elif a["type"] == "reply_text":
                    self.assertEqual(a.get("regex"), ".+", "reply 锚点只能是结构通配")
                else:
                    self.fail("unexpected card anchor type " + str(a["type"]))
                self.assertEqual(a["expect"], "present")
                self.assertEqual(a["severity"], "hard")
                seen += 1
        self.assertGreater(seen, 75)
        ops = {c["op"] for row in self.plan["samples"] if row["sample_id"] in compiled_ids
               for c in row["checks"] if c["layer"] == "objective"}
        self.assertTrue(ops <= {"file_exists"}, ops)
        # 检查项文本必须一字来自卡片要求（不新造判据句）
        for row in self.plan["samples"]:
            if row["sample_id"] not in compiled_ids:
                continue
            for c in row["checks"]:
                if c["layer"] == "semantic":
                    self.assertIn(c["requirement"][:40], card_text[row["sample_id"]], c["id"])

    def test_memory_and_reply_requirements_become_mandatory_checks(self):
        src = self.matrix["evidence_sources"]["semantic_scopes"]
        self.assertGreater(src["memory_after"]["card_compiled"], 60,
                           "记忆类要求必须由结构锚点溯源成真检查项")
        self.assertGreater(src["agent_reply"]["card_compiled"], 40)
        self.assertEqual(src["memory_before"]["card_compiled"], 0,
                         "before 快照不能靠结构锚点假称已观测")
        reasons = self.matrix["compile_report"]["requirement_gap_reasons"]
        self.assertNotIn("no_anchor_carrier", reasons,
                         "结构锚点可用时不该再把 memory/reply 要求记成缺口")
        self.assertIn("no_artifact_path", reasons)

    def test_rejected_cards_carry_reason_codes_from_the_documented_set(self):
        codes = set(F.REASON_CODES)
        all_rows = self.matrix["compile_report"]["uncompilable_cards"] + \
            self.matrix["compile_report"]["cap_rejected_cards"]
        self.assertTrue(all_rows, "cards must not silently compile: 0 fixture / no paths 卡必须被拒")
        for r in all_rows:
            self.assertIn(r["primary_reason"], codes)
            self.assertTrue(set(r["all_reasons"]) <= codes)

    def test_pilot_merge_is_byte_identical_and_extra_suites_are_ledgered(self):
        tail = F.pilot_block((ROOT / "samples/pilot-v0.2.yaml").read_text(encoding="utf-8"))
        self.assertIn(tail, self.paper_yaml, "pilot 原文块必须逐字节出现在卷内")
        self.assertIs(self.matrix["pilot_merge"]["byte_identical_yaml"], True)
        self.assertTrue(self.paper_yaml.endswith(tail) or self.matrix["inputs"]["extra_suite_shifts"],
                        "有 --extra-suite 时卷尾让给最后一个题源，必须有台账说明")
        shifts = self.matrix["inputs"]["extra_suite_shifts"]
        self.assertEqual(len(shifts), 1)
        self.assertEqual(shifts[0]["samples"], 5)
        self.assertIs(shifts[0]["field_equal_verified"], True,
                      "0 缩进题源只能整体右移 2 空格并入，且必须逐样本 deep-equal 验证")
        self.assertNotEqual(shifts[0]["raw_sha256"], shifts[0]["merged_sha256"])

    def test_pilot_plan_rows_authored_and_derived_are_distinguished(self):
        gap = self.matrix["gaps"]["pilot_samples_without_plan_row"]
        self.assertEqual(gap["authored_rows_copied"], 2, "baseline 的 2 行人工 disposition 原样复制")
        non_card = self.matrix["totals"]["paper_samples"] - self.matrix["totals"]["compiled_samples"]
        self.assertEqual(gap["derived_rows"], non_card - gap["authored_rows_copied"],
                         "非卡片题源 = pilot 20 + batchA 5，减去 baseline 已有的 2 行")
        self.assertEqual(gap["authored_rows_copied"], 2)
        self.assertEqual(gap["count"], 0, "派生后每条题源样本都有强制检查集")
        self.assertEqual(gap["sample_ids"], [])
        anchors = self.matrix["gaps"]["pilot_anchor_gaps_by_reason"]
        self.assertEqual(anchors["total_anchors_without_mandatory_check"], 18)
        self.assertEqual(anchors["histogram"], {"on_violation_undeclared": 13,
                                                "event_scope_not_representable": 5},
                         "派生不出来的必须逐因留痕，而不是悄悄丢掉")

    def test_derived_checks_copy_anchor_fields_verbatim(self):
        """requirement 与 on_failure 一律取锚点原字段：本工具不做任何语义裁决。"""
        pilot = {s["id"]: s for s in yaml.safe_load(
            (ROOT / "samples/pilot-v0.2.yaml").read_text(encoding="utf-8"))["samples"]}
        authored = {r["sample_id"] for r in json.loads(
            (ROOT / "samples/four-layer-pilot.v1.json").read_text(encoding="utf-8"))["samples"]}
        seen = 0
        for row in self.plan["samples"]:
            sid = row["sample_id"]
            if sid not in pilot or sid in authored:
                continue
            by_aid = {a["id"]: a for a in pilot[sid]["anchors"]}
            for chk in row["checks"]:
                prefix = "pilot_" + sid + "_"
                self.assertTrue(chk["id"].startswith(prefix), chk["id"])
                aid = chk["id"][len(prefix):]
                aid = aid[:-7] if aid.endswith("_exists") else (
                      aid[:-8] if aid.endswith("_content") else aid)
                self.assertIn(aid, by_aid, chk["id"])
                a = by_aid[aid]
                self.assertEqual(chk["on_failure"], a["on_violation"], chk["id"])
                if chk["layer"] == "semantic":
                    self.assertEqual(chk["requirement"], a["desc"].strip(), chk["id"])
                    self.assertIn("anchor " + aid, chk["basis"])
                seen += 1
        self.assertGreater(seen, 80, "派生检查项应覆盖绝大多数锚点（实测 89）")

    def test_event_anchors_never_become_memory_checks(self):
        pilot = {s["id"]: s for s in yaml.safe_load(
            (ROOT / "samples/pilot-v0.2.yaml").read_text(encoding="utf-8"))["samples"]}
        forbidden = set()
        for sid, s in pilot.items():
            for a in s["anchors"]:
                if a["type"] in ("memory_read", "memory_write", "tool_call"):
                    forbidden.add("pilot_%s_%s" % (sid, a["id"]))
        ids = {c["id"] for row in self.plan["samples"] for c in row["checks"]}
        self.assertTrue(forbidden & ids == set(), sorted(forbidden & ids))
        self.assertEqual(self.matrix["gaps"]["pilot_anchor_gaps_by_reason"]
                         ["histogram"]["event_scope_not_representable"], 5)

    def test_no_derive_reproduces_the_two_row_baseline(self):
        out = self.root / "f_no_derive"
        out.mkdir(parents=True)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["--no-derive-pilot-rows"], out), 0)
        old = last_json(buf.getvalue())
        self.assertEqual(old["plan_rows"], old["compiled"] + 2)
        self.assertEqual(old["plan_rows_derived_pilot"], 0)
        self.assertGreater(old["compiled"], 60)

    def test_objective_ops_are_only_file_exists(self):
        ops = {c["op"] for row in self.plan["samples"] for c in row["checks"] if c["layer"] == "objective"}
        self.assertEqual(ops, {"file_exists"},
                         "pilot/卡片都没有可比对初始工件正文 ⇒ 只能用 file_exists（其余 op 需 fixture 正文）")

    def test_dimension_coverage_is_disclosed_both_ways(self):
        g = self.matrix["gaps"]["dimensions_below_min_per_dimension"]
        self.assertEqual(g["dimensions_below_by_plan_rows"], [], "六维都该有强制检查集")
        dims = self.matrix["dimensions"]
        self.assertEqual(sum(v["plan_rows"] for v in dims.values()), self.matrix["totals"]["plan_rows"])
        self.assertEqual(sum(v["checks_objective"] + v["checks_semantic"] for v in dims.values()),
                         self.matrix["totals"]["plan_checks"], "分维计数必须等于总量")
        for d, v in dims.items():
            self.assertGreaterEqual(v["plan_rows"], 1, d)
            self.assertLessEqual(v["plan_rows_from_cards_only"], v["plan_rows"], d)
        self.assertEqual(dims["dynamic_update"]["plan_rows_from_cards_only"], 0,
                         "17 张 update 相位卡被三相枚举挡住 ⇒ 必须留在台账里")
        self.assertIn("paper_phase_enum_limitation", self.matrix["gaps"])
        self.assertEqual(self.matrix["totals"]["paper_samples"], 100, "出货口径 = 100 题")
        self.assertEqual(self.matrix["totals"]["plan_rows"], 100)

    def test_rerun_is_byte_identical(self):
        out2 = self.root / "b"
        out2.mkdir(parents=True)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run([], out2), 0)
        for name in ("paper.yaml", "plan.json", "paper.jsonl"):
            self.assertEqual((self.out / name).read_bytes(), (out2 / name).read_bytes(), name)

    def test_require_human_review_rejects_every_card(self):
        out3 = self.root / "c"
        out3.mkdir(parents=True)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["--require-human-review"], out3), 0)
        summary = last_json(buf.getvalue())
        self.assertEqual(summary["compiled"], 0, "全部候选卡 review.* 仍有 null ⇒ 一张都不能进卷")
        self.assertEqual(sum(summary["primary_reason_histogram"].values()),
                         summary["cards_total"], "每张卡都必须有一个主因")
        self.assertGreaterEqual(summary["primary_reason_histogram"]["human_review_pending"], 90,
                                "其余卡片在此之前已被更早的闸拒掉（闸序见模块 docstring）")

    def test_time_whitelist_is_replacement_not_additive(self):
        """--allow-time-mode 是白名单**整体替换**：只给 actual_elapsed 会把 94 张
        cross_session_only 卡拒在时间闸上。这是操作者必须知道的语义，锁进测试。"""
        out = self.root / "d_replacement"
        out.mkdir(parents=True)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["--allow-time-mode", "actual_elapsed"], out, shipped=False), 0)
        only_actual = last_json(buf.getvalue())
        # 期望值从卡包实算，不写死：白名单只放 actual_elapsed ⇒ 其余 mode 的卡全部撞时间闸
        pkg = json.loads(CARDS.read_text(encoding="utf-8"))
        expected = sum(1 for c in pkg["cards"] if c["time_semantics"]["mode"] != "actual_elapsed")
        self.assertEqual(only_actual["primary_reason_histogram"]["time_semantics_unsupported"],
                         expected)
        self.assertLess(expected, only_actual["cards_total"], "至少得有一种卡能过时间闸")
        self.assertEqual(only_actual["compiled"], 0,
                         "过了时间闸的卡仍然没有可定位产物路径 ⇒ 一张都进不了卷")

    def test_opening_all_time_modes_and_switches_does_not_lose_compiled_cards(self):
        out4 = self.root / "d"
        out4.mkdir(parents=True)
        import contextlib
        import io
        buf = io.StringIO()
        args = ["--allow-time-mode", "cross_session_only", "--allow-time-mode", "actual_elapsed",
                "--allow-time-mode", "logical_calendar_reference", "--allow-restart",
                "--include-long-wait"]
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(args, out4), 0)
        wider = last_json(buf.getvalue())
        self.assertGreaterEqual(wider["compiled"], self.summary["compiled"],
                                "放开闸只会让更多卡进候选池，不会让已过的卡掉出去")
        for code in ("time_semantics_unsupported", "requires_process_restart", "long_real_wait"):
            self.assertNotIn(code, wider["primary_reason_histogram"], code)

    def test_count_and_per_dimension_caps_take_effect(self):
        out5 = self.root / "e"
        out5.mkdir(parents=True)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["--count", "3"], out5), 0)
        capped = last_json(buf.getvalue())
        self.assertEqual(capped["compiled"], 3)
        self.assertGreaterEqual(capped["cap_rejected"], 16)
        self.assertEqual(capped["plan_rows"], capped["compiled"] + 2 + 23,
                         "卡片行受 --count 限制；25 条题源（2 行人工 + 23 行派生）不受影响")


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
