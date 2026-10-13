#!/usr/bin/env python3
"""混淆判据派生（v1.0-dev.2）与红线1 三读法的守卫。

守的是四件容易被"顺手改回去"的事：
  ① **默认关**：`--error-control-checks` 不开时，编译器必须仍产出 dev.1 的计划
     （否则已广播的 dev.1 与已签发的人审答案会静默作废）；
  ② **卷一个字节不变**：派生只动四层计划；`samples/paper-r1.yaml` / `build/paper-r1.jsonl`
     的 sha 必须仍是 dev.1 的锁值（这是"今晚采的证据可以重评分复用"这条承诺的物质基础）；
  ③ **派生可失败**：三条资格各自独立能拒、载体缺失能拒、插入位置错了能被抓到
     （mutation 用例，无自我豁免）——永远绿的自检是最危险的产物；
  ④ **三读法都在**：验收读法 C 达标不得让 A/B 的低值从凭据里消失
     （挑好看的报是造假，见 notes/p5-preregistration-v1.0.md §8 R-2）。

题源卡片包不在本机时整套 skip（公开口径下私有材料可以缺席），不假红也不假绿。
"""
import collections
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

import testtmp                                                  # noqa: E402
import freeze_gates as G                                        # noqa: E402
import freeze_status as FS                                      # noqa: E402
from loader import load_jsonl                                   # noqa: E402
from scoring.four_layer import validate_plan                    # noqa: E402

CARDS = pathlib.Path(r"D:/a_shanghai/_review/four-layer/s8-batch18-candidate-design"
                     "/cards-r3/candidate-cards.json")
PAPER_YAML = ROOT / "samples/paper-r1.yaml"
PAPER_JSONL = ROOT / "build/paper-r1.jsonl"
PLAN_DEV1 = ROOT / "samples/four-layer-r1.json"
PLAN_DEV2 = ROOT / "samples/four-layer-r1-dev2.json"
MATRIX_DEV2 = ROOT / "notes/paper-r1-dev2-coverage-matrix.json"
STATUS_DEV1 = ROOT / "notes/freeze-status-v1.0.json"
STATUS_DEV2 = ROOT / "notes/freeze-status-v1.0-dev2.json"

R1_PAPER_SHA = "f30541ae271690a5d07511a5260a22386224bcadd753e075da40cf788ff5cc60"
R1_JSONL_SHA = "0da837550437684b3ac8eec7c415faa5c0b65d0629cf777ad3d1be3d9d1f7867"
R1_PLAN_DEV1_SHA = "5bed443dabb46be33a84cbff19d490c1c7d6832654bf8d855e99fa1b6329db97"
R1_PLAN_DEV2_SHA = "a96e5bbfb07176aaa99473b3febb786c703a623b27332b4d06d4b708c55ff1c9"
DERIVED_TOTAL = 44
THRESHOLD = 8

COMPILE_ARGS = ["tools/freeze_candidates.py", "--extra-suite", "samples/v1.0-batchA.yaml",
                "--allow-restart", "--include-long-wait",
                "--allow-time-mode", "cross_session_only", "--allow-time-mode", "actual_elapsed",
                "--allow-time-mode", "logical_calendar_reference", "--target-samples", "100"]


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def env_with_deps() -> dict:
    import os
    env = dict(os.environ)
    deps = str(pathlib.Path(r"D:/a_shanghai/_review/four-layer/takeover-deps"))
    extra = [str(ROOT), str(ROOT / "tools")]
    if pathlib.Path(deps).is_dir():
        extra.insert(0, deps)
    env["PYTHONPATH"] = os.pathsep.join(extra + [env.get("PYTHONPATH", "")]).strip(os.pathsep)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def card(**over) -> dict:
    """一张最小合成卡：字段名与 cards-r3 一致，便于逐条改一个字段做 mutation。"""
    c = {"id": "synthetic_001", "dimension": "memory_recall", "boundary": None,
         "authored_error_control": {"example": "回答杉桥用PDF。", "synthetic": True,
                                    "human_truth_label": None},
         "allowed_equivalents": ["语义等价表述允许", "不使用禁词或字符串出现就判错"],
         "requirements": [{"id": "R_TASK", "scope": "reply_final", "proposed_failure": "omitted",
                           "requirement": "回答杉桥采用Markdown，不交换项目绑定。",
                           "basis": ["s1.t1"]}]}
    c.update(over)
    return c


def checks_with_carrier(scope="agent_reply"):
    """一份最小 checks/disposition，形状与 compile_card 产出一致。"""
    if scope == "agent_reply":
        shadow = {"id": "R_R_TASK", "layer": "semantic", "scope": "agent_reply",
                  "session": "s3", "turn": 1, "requirement": "回答杉桥采用Markdown。",
                  "on_failure": "omitted", "basis": "s1.t1"}
    elif scope == "file_final":
        shadow = {"id": "A1_content", "layer": "semantic", "scope": "file_final",
                  "path": "日报.md", "requirement": "三段顺序正确。",
                  "on_failure": "omitted", "basis": "s1.t1"}
    else:
        shadow = {"id": "R_R_MEMORY", "layer": "semantic", "scope": "memory_after",
                  "requirement": "记忆面支持当前规则。", "on_failure": "omitted",
                  "basis": "s1.t1"}
    checks = [shadow]
    disp = {"A1": {"role": "semantic", "checks": [shadow["id"]], "reason": "structural"}}
    return checks, disp, shadow


class EligibilityTests(unittest.TestCase):
    """三条资格：每条都必须**单独**能把派生拒掉（否则资格就是装饰）。"""

    def test_all_three_pass(self):
        ok, why = G.error_control_eligibility(card())
        self.assertTrue(ok, why)
        self.assertEqual(why, {"has_error_exemplar": True,
                               "no_content_error_class_declared": True,
                               "not_a_boundary_card": True})

    def test_missing_exemplar_rejects(self):
        for ec in ({}, {"example": "   "}, {"example": None}, None):
            ok, why = G.error_control_eligibility(card(authored_error_control=ec))
            self.assertFalse(ok, ec)
            self.assertFalse(why["has_error_exemplar"], ec)

    def test_declared_content_error_class_rejects(self):
        for bad in ("confused", "wrongly_persisted", "wrongly_reused"):
            c = card(requirements=[{"id": "R_TASK", "scope": "reply_final",
                                    "proposed_failure": bad, "requirement": "x", "basis": ["s1.t1"]}])
            ok, why = G.error_control_eligibility(c)
            self.assertFalse(ok, bad)
            self.assertFalse(why["no_content_error_class_declared"], bad)

    def test_boundary_card_rejects(self):
        for kind in ("withhold", "release"):
            ok, why = G.error_control_eligibility(card(boundary=kind))
            self.assertFalse(ok, kind)
            self.assertFalse(why["not_a_boundary_card"], kind)


class DerivationTests(unittest.TestCase):
    def test_derives_confused_check_before_shadow(self):
        checks, disp, shadow = checks_with_carrier("agent_reply")
        rec = G.attach_error_control_check(card(), checks, disp)
        self.assertTrue(rec["derived"], rec)
        self.assertEqual(len(checks), 2)
        # 顺序是判类的决定因素（four_layer.py: verdict = failed[0]['on_failure']）
        self.assertEqual(checks[0]["id"], "R_R_TASK_CONF")
        self.assertEqual(checks[0]["on_failure"], "confused")
        self.assertEqual(checks[1]["id"], shadow["id"])
        # 载体锚点必须能溯源到新检查（validate_plan 的 used == set(checks)）
        self.assertEqual(disp["A1"]["checks"], ["R_R_TASK", "R_R_TASK_CONF"])

    def test_mutation_appending_instead_of_inserting_is_detected(self):
        """变异样本：把派生检查放到载体之后 ⇒ 张冠李戴会被 omitted 吃掉，本用例必须红。"""
        checks, disp, shadow = checks_with_carrier("agent_reply")
        rec = G.attach_error_control_check(card(), checks, disp)
        self.assertTrue(rec["derived"])
        mutated = [checks[1], checks[0]]            # 交换顺序＝变异
        self.assertNotEqual([c["id"] for c in mutated], ["R_R_TASK_CONF", "R_R_TASK"],
                            "变异后顺序仍与期望相同 ⇒ 本守卫其实判不出顺序，是永远绿的假自检")
        # 判类后果：第一条命中的是 omitted，confused 永远数不到
        self.assertEqual(mutated[0]["on_failure"], "omitted")

    def test_carrier_scope_precedence_and_field_shape(self):
        # reply 与 file 同在时优先 reply（混淆在探测轮回复里可观察）
        checks, disp, _ = checks_with_carrier("file_final")
        reply = {"id": "R_R_TASK", "layer": "semantic", "scope": "agent_reply",
                 "session": "s3", "turn": 1, "requirement": "r", "on_failure": "omitted",
                 "basis": "s1.t1"}
        checks.append(reply)
        disp["A2"] = {"role": "semantic", "checks": ["R_R_TASK"], "reason": "structural"}
        rec = G.attach_error_control_check(card(), checks, disp)
        self.assertEqual(rec["scope"], "agent_reply")
        got = next(c for c in checks if c["id"] == "R_R_TASK_CONF")
        self.assertEqual(set(got), {"id", "layer", "scope", "session", "turn", "requirement",
                                    "on_failure", "basis"})
        self.assertEqual((got["session"], got["turn"]), ("s3", 1))

    def test_file_final_carrier_keeps_path_and_names_it(self):
        checks, disp, _ = checks_with_carrier("file_final")
        rec = G.attach_error_control_check(card(), checks, disp)
        got = next(c for c in checks if c["id"] == "A1_content_CONF")
        self.assertEqual(got["path"], "日报.md")
        self.assertIn("产物 日报.md", got["requirement"])
        self.assertEqual(rec["scope"], "file_final")

    def test_requirement_text_carries_exemplar_and_equivalents_verbatim(self):
        checks, _, _ = checks_with_carrier("agent_reply")
        c = card()
        G.attach_error_control_check(c, checks, dict(A1={"role": "semantic",
                                                         "checks": ["R_R_TASK"], "reason": "x"}))
        text = checks[0]["requirement"]
        self.assertIn(c["authored_error_control"]["example"].strip(), text)
        for eq in c["allowed_equivalents"]:
            self.assertIn(eq, text)
        # 遗漏不得被本条判违反，否则 confused 会把 omitted 吃掉（定类污染）
        self.assertIn("omitted", text)
        # 禁止退化成字符串判据（卡片包与 CONTRACT §5 的同源约束）
        self.assertIn("字面字符串命中不作为判据", text)

    def test_no_carrier_check_is_recorded_not_invented(self):
        checks, disp = [], {}
        rec = G.attach_error_control_check(card(), checks, disp)
        self.assertFalse(rec["derived"])
        self.assertEqual(rec["skip_reason"], "no_semantic_carrier_check")
        self.assertEqual(checks, [])

    def test_objective_only_carrier_is_refused(self):
        """载体必须是 semantic 检查：objective 检查没有 requirement，派生上去就是编判据。"""
        obj = {"id": "A1_exists", "layer": "objective", "op": "file_exists", "path": "日报.md",
               "on_failure": "omitted", "basis": "s1.t1"}
        checks = [obj]
        disp = {"A1": {"role": "objective", "checks": ["A1_exists"], "reason": "x"}}
        rec = G.attach_error_control_check(card(), checks, disp)
        self.assertFalse(rec["derived"])
        self.assertEqual(rec["skip_reason"], "no_semantic_carrier_check")
        self.assertEqual(len(checks), 1)


@unittest.skipUnless(CARDS.is_file() and PAPER_JSONL.is_file() and PLAN_DEV1.is_file(),
                     "缺卡片包 / dev.1 卷产物（build/ 未同步）/ dev.1 计划 —— 干净环境如实跳过")
class Dev2ArtifactTests(unittest.TestCase):
    """dev.2 = dev.1 的卷（逐字节不变）+ 新计划。这四条是 A 侧复用今晚证据的前提。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(testtmp.mkdtemp(prefix="okb-dev2-"))
        cls.out = {k: cls.tmp / k for k in ("paper.yaml", "plan.json", "paper.jsonl", "matrix.json")}
        cmd = [sys.executable] + COMPILE_ARGS + ["--error-control-checks",
               "--out-paper", str(cls.out["paper.yaml"]), "--out-plan", str(cls.out["plan.json"]),
               "--out-jsonl", str(cls.out["paper.jsonl"]), "--out-matrix", str(cls.out["matrix.json"])]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, encoding="utf-8",
                              errors="replace", timeout=900, env=env_with_deps())
        cls.proc = proc

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_compile_succeeded(self):
        self.assertEqual(self.proc.returncode, 0,
                         (self.proc.stdout or "")[-800:] + (self.proc.stderr or "")[-800:])

    def test_dev2_plan_is_byte_reproducible(self):
        self.assertTrue(PLAN_DEV2.is_file(), "缺 samples/four-layer-r1-dev2.json")
        self.assertEqual(sha(self.out["plan.json"]), R1_PLAN_DEV2_SHA, "dev.2 计划不可复现")
        self.assertEqual(sha(PLAN_DEV2), R1_PLAN_DEV2_SHA, "已入库的 dev.2 计划与锁值不符")

    def test_paper_bytes_unchanged_by_derivation(self):
        """派生只许动计划：卷与题卡源必须仍是 dev.1 的 sha（A 的 paper_sha256 锁靠这条）。"""
        self.assertEqual(sha(self.out["paper.yaml"]), R1_PAPER_SHA, "题卡源被派生改动了")
        self.assertEqual(sha(self.out["paper.jsonl"]), R1_JSONL_SHA, "编译卷被派生改动了")
        self.assertTrue(PAPER_YAML.is_file() and PAPER_JSONL.is_file(),
                        "本机缺 dev.1 卷产物（build/ 未同步）——先跑 build_paper")
        self.assertEqual(sha(PAPER_YAML), R1_PAPER_SHA)
        self.assertEqual(sha(PAPER_JSONL), R1_JSONL_SHA)

    def test_dev1_plan_untouched(self):
        self.assertEqual(sha(PLAN_DEV1), R1_PLAN_DEV1_SHA, "dev.1 计划被改动 ⇒ 已广播件失效")

    def test_dev2_plan_validates_against_dev1_paper(self):
        plan = json.loads(self.out["plan.json"].read_text(encoding="utf-8"))
        paper = load_jsonl(str(self.out["paper.jsonl"]))
        validate_plan(plan, paper, R1_JSONL_SHA)      # 抛异常即失败
        self.assertEqual(plan["paper_sha256"], R1_JSONL_SHA)

    def test_derived_count_and_matrix_agree(self):
        mat = json.loads(self.out["matrix.json"].read_text(encoding="utf-8"))
        ec = mat["error_control_derivation"]
        self.assertTrue(ec["enabled"])
        self.assertEqual(ec["derived_total"], DERIVED_TOTAL)
        self.assertEqual(ec["eligible_total"], DERIVED_TOTAL,
                         "合格卡里出现派生不出的 ⇒ 载体缺失，必须逐条看 skip_reason")
        plan = json.loads(self.out["plan.json"].read_text(encoding="utf-8"))
        dev1 = json.loads(PLAN_DEV1.read_text(encoding="utf-8"))
        self.assertEqual(sum(len(r["checks"]) for r in plan["samples"])
                         - sum(len(r["checks"]) for r in dev1["samples"]), DERIVED_TOTAL)
        ids = [c["id"] for r in plan["samples"] for c in r["checks"] if c["id"].endswith("_CONF")]
        self.assertEqual(len(ids), DERIVED_TOTAL)
        # id 只要求**样本内**唯一（validate_plan 的口径）；跨样本重名与 dev.1 的 R_R_TASK 同款，
        # 上一版这里断言全局唯一，是我把口径写错了（44 条派生只有 2 个不同 id）。
        for row in plan["samples"]:
            row_ids = [c["id"] for c in row["checks"]]
            self.assertEqual(len(set(row_ids)), len(row_ids),
                             "%s 样本内检查 id 冲突" % row["sample_id"])
        # 逐卡台账必须齐（人审要按它复核；缺台账＝不可核）
        self.assertEqual(len(ec["per_card"]), 75, "台账必须覆盖全部卡片编译题")
        for rec in ec["per_card"]:
            self.assertIn("eligibility", rec)
            if not rec["derived"]:
                self.assertIn("skip_reason", rec)
        shipped = json.loads(MATRIX_DEV2.read_text(encoding="utf-8"))
        self.assertEqual(shipped["error_control_derivation"]["derived_total"], DERIVED_TOTAL)

    def test_every_derived_check_quotes_its_own_card_exemplar(self):
        """派生只许用卡片自己登记的错误对照；出现无出处的错值就是编判据。"""
        cards = {c["id"]: c for c in json.loads(CARDS.read_text(encoding="utf-8"))["cards"]}
        plan = json.loads(self.out["plan.json"].read_text(encoding="utf-8"))
        n = 0
        for row in plan["samples"]:
            for c in row["checks"]:
                if not c["id"].endswith("_CONF"):
                    continue
                n += 1
                card = cards[row["sample_id"]]
                self.assertIn(card["authored_error_control"]["example"].strip(), c["requirement"],
                              row["sample_id"])
                for eq in card["allowed_equivalents"]:
                    self.assertIn(eq, c["requirement"], row["sample_id"])
        self.assertEqual(n, DERIVED_TOTAL)


@unittest.skipUnless(CARDS.is_file() and PLAN_DEV2.is_file() and PAPER_JSONL.is_file()
                     and STATUS_DEV1.is_file() and STATUS_DEV2.is_file(),
                     "缺卡片包 / dev.2 计划 / dev.1 卷产物 / 就绪度凭据")
class RedLine1ReadingTests(unittest.TestCase):
    """三读法：验收读法达标不得让另外两个读法的低值消失。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = load_jsonl(str(PAPER_JSONL))
        cls.dev1 = json.loads(PLAN_DEV1.read_text(encoding="utf-8"))
        cls.dev2 = json.loads(PLAN_DEV2.read_text(encoding="utf-8"))

    def test_reading_c_passes_on_dev2(self):
        m = FS.measure(self.rows, self.dev2)
        per = m["red_line_1_confusion"]["per_dimension"]
        for d in FS.DIMENSIONS:
            self.assertGreaterEqual(per[d]["plan_content_error_checks"], THRESHOLD,
                                    "%s 在验收读法 C 下仍不达标" % d)

    def test_reading_a_still_fails_on_dev2_and_is_still_published(self):
        """A（预注册 §3.1 字面）在 dev.2 上仍是 2/6：不许因为 C 达标就把它从凭据里抹掉。"""
        m = FS.measure(self.rows, self.dev2)
        rl = m["red_line_1_confusion"]
        self.assertEqual(rl["dims_ok"], 2, "A 读法达标维数变了 ⇒ 卷或锚点被改动")
        self.assertEqual(rl["readings"]["A_anchors_literal"]["dims_ok"], 2)
        self.assertEqual(rl["readings"]["B_plan_confused"]["dims_ok"], 5)
        self.assertEqual(rl["readings"]["C_plan_content_error"]["dims_ok"], 6)
        self.assertEqual(rl["acceptance_reading"], "C_plan_content_error")
        # boundary_recognition 在 B 读法下不达标，且必须写明是设计使然而非漏做
        self.assertEqual(rl["per_dimension"]["boundary_recognition"]["plan_confused_checks"], 2)
        self.assertIn("CONTRACT §11", rl["readings"]["B_plan_confused"]["note"])

    def test_dev1_numbers_unchanged_by_the_new_readings(self):
        """dev.1 的既有读数一个字都不许变（凭据是纯增量更新）。"""
        st = json.loads(STATUS_DEV1.read_text(encoding="utf-8"))
        rl = st["red_line_1_confusion"]
        self.assertEqual(rl["dims_ok"], 2)
        self.assertEqual(rl["per_dimension"]["memory_recall"]["confusion_judgements"], 1)
        self.assertEqual(len(st["red_lines_failed"]), 6, "dev.1 仍是 6 条红线全未过")
        self.assertEqual(st["paper_sha256"], R1_JSONL_SHA)

    def test_dev2_credential_matches_recomputation(self):
        st = json.loads(STATUS_DEV2.read_text(encoding="utf-8"))
        self.assertEqual(st["plan"], "samples/four-layer-r1-dev2.json")
        self.assertEqual(st["paper_sha256"], R1_JSONL_SHA)
        self.assertEqual(st["sources"]["plan_sha256"], R1_PLAN_DEV2_SHA)
        self.assertEqual(st["red_line_1_confusion"]["readings"]
                         ["C_plan_content_error"]["dims_ok"], 6)
        self.assertNotIn("红线1 每维混淆判据>=8", st["red_lines_failed"])
        self.assertEqual(len(st["red_lines_failed"]), 5, "dev.2 只过了红线1，其余 5 条仍未过")

    def test_mutation_a_dim_below_threshold_is_detected(self):
        """变异样本：抽掉一维的派生检查 ⇒ C 读法必须判它不达标（证明这条守卫能失败）。"""
        m = FS.measure(self.rows, self.dev2)
        per = m["red_line_1_confusion"]["per_dimension"]
        target = "memory_recall"
        mutated = json.loads(json.dumps(self.dev2, ensure_ascii=False))
        removed = 0
        for row in mutated["samples"]:
            if row["sample_id"] in {r["id"] for r in self.rows
                                    if r["dimension"] == target}:
                keep = [c for c in row["checks"] if not c["id"].endswith("_CONF")]
                removed += len(row["checks"]) - len(keep)
                row["checks"] = keep
        self.assertGreater(removed, 0, "变异没抽掉任何检查 ⇒ 本用例是空转")
        m2 = FS.measure(self.rows, mutated)
        self.assertLess(m2["red_line_1_confusion"]["per_dimension"][target]
                        ["plan_content_error_checks"], THRESHOLD,
                        "抽掉派生检查后仍判达标 ⇒ C 读法数不到派生检查，守卫是假的")
        self.assertLess(m2["red_line_1_confusion"]["readings"]
                        ["C_plan_content_error"]["dims_ok"], 6)
        self.assertEqual(per[target]["plan_content_error_checks"], 18, "原值漂移")

    def test_plan_and_paper_must_be_same_source(self):
        """卷与计划不同源时必须拒绝出凭据（退出 2），不是静默出个错数。"""
        bad = json.loads(json.dumps(self.dev2, ensure_ascii=False))
        bad["paper_sha256"] = "0" * 64
        tmp = pathlib.Path(testtmp.mkdtemp(prefix="okb-badplan-"))
        self.addCleanup(lambda: shutil.rmtree(str(tmp), ignore_errors=True))
        (tmp / "bad-plan.json").write_text(json.dumps(bad, ensure_ascii=False),
                                           encoding="utf-8", newline="\n")
        proc = subprocess.run([sys.executable, "tools/freeze_status.py",
                               "--plan", str(tmp / "bad-plan.json"), "--out", str(tmp / "s.json")],
                              cwd=str(ROOT), capture_output=True, encoding="utf-8",
                              errors="replace", timeout=300, env=env_with_deps())
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertFalse((tmp / "s.json").exists(), "不同源却写出了凭据")


@unittest.skipUnless((ROOT / "runs/e2e100/manifest.json").is_file() and PAPER_JSONL.is_file()
                     and PLAN_DEV2.is_file(),
                    "缺 runs/e2e100 离线证据（不在 VM 同步范围内）/ dev.1 卷 / dev.2 计划")
class DerivedCheckLivenessTests(unittest.TestCase):
    """44 条派生检查必须**条条可判**，且不得扰动确定性层。

    新增判据最坏的失败模式不是"判错"，而是"永远判不出"——预注册 §3.4 红线4 的"零死锚"
    在计划层的对应物。离线 prepare 模式不叫判官（语义层一律 insufficient，这是既有语义），
    但足以证明两件事：
      ① 每条派生检查都能装出证据、产出判官请求（`semantic-requests.json` 里有它），
         不是结构性不可判；
      ② objective 层（layer2）读数与 dev.1 **逐格相同** ⇒ 本次改动没碰任何确定性判据。
    证据：`runs/e2e100` = 100 题 × 单 mock agent（tier=remember）× 2 轮 = 200 任务，
    由 `tools/run_suite.py` 之外的既有离线链路产出（README「当前状态」表第 1 行）。
    """

    def _score(self, plan_rel: str, tag: str) -> dict:
        out = self.tmp / tag
        proc = subprocess.run(
            [sys.executable, "tools/score_four_layer.py", "--paper", "build/paper-r1.jsonl",
             "--plan", plan_rel, "--run-dir", "runs/e2e100", "--out-dir", str(out),
             "--outcome-version", "v2", "--evidence-policy", "scoped-v2"],
            cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace",
            timeout=900, env=env_with_deps())
        # rc=1 是 prepare 模式的正常出口（语义层未裁决），不是失败；rc=0/1 之外才算跑挂
        self.assertIn(proc.returncode, (0, 1), (proc.stdout or "")[-500:] + (proc.stderr or "")[-500:])
        self.assertTrue((out / "results.json").is_file(), "评分没落盘")
        return {n: json.loads((out / n).read_text(encoding="utf-8"))
                for n in ("results.json", "semantic-requests.json", "summary.json", "complete.json")}

    def setUp(self):
        self.tmp = pathlib.Path(testtmp.mkdtemp(prefix="okb-dev2-live-"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))

    def test_derived_checks_are_live_and_objective_layer_unchanged(self):
        d1 = self._score("samples/four-layer-r1.json", "dev1")
        d2 = self._score("samples/four-layer-r1-dev2.json", "dev2")
        man = json.loads((ROOT / "runs/e2e100/manifest.json").read_text(encoding="utf-8"))
        rounds = len(man["tasks"]) // 100
        self.assertGreaterEqual(rounds, 1, "e2e100 的任务数不足以推出轮次")

        # ① 每条派生检查都产出了判官请求（条数 = 派生数 × 轮次）
        req1 = {(r["task_id"], r["check_id"]) for r in d1["semantic-requests.json"]}
        req2 = {(r["task_id"], r["check_id"]) for r in d2["semantic-requests.json"]}
        conf = {(r["task_id"], r["check_id"]) for r in d2["semantic-requests.json"]
                if r["check_id"].endswith("_CONF")}
        self.assertEqual(len(req2) - len(req1), DERIVED_TOTAL * rounds,
                         "新增请求数 != 派生检查数 × 轮次 ⇒ 有派生检查装不出证据（死判据）")
        self.assertEqual(len(conf), DERIVED_TOTAL * rounds,
                         "有派生检查没产出请求：%d/%d" % (len(conf), DERIVED_TOTAL * rounds))
        # 派生检查 id 只要求**样本内**唯一（跨样本重名与 dev.1 的 R_R_TASK 同款），
        # 所以"44 条都活着"要按 (样本, 检查 id) 数，不能按 id 字面数（第一版在这里写错过）。
        task2sample = {r["task_id"]: r["sample_id"] for r in d2["results.json"]}
        pairs = {(task2sample[t], c) for t, c in conf}
        self.assertEqual(len(pairs), DERIVED_TOTAL,
                         "有派生检查没产出请求：%d/%d" % (len(pairs), DERIVED_TOTAL))
        self.assertEqual(len({sid for sid, _ in pairs}), DERIVED_TOTAL,
                         "派生检查覆盖的样本数不对（应一卡一条）")

        # ② objective 层逐格相同（我的改动只加语义检查，不许碰确定性判据）
        def layer2(rows):
            c = collections.Counter()
            for row in rows:
                for x in row.get("layer2") or []:
                    c[(row["sample_id"], row["task_id"], x["id"], x["result"])] += 1
            return c
        self.assertEqual(layer2(d1["results.json"]), layer2(d2["results.json"]),
                         "objective 层读数被派生改动了")

        # ③ prepare 模式的汇总读数不变（语义层未裁决 ⇒ 派生只增加待判请求，不改现有结论）
        self.assertEqual(d1["complete.json"]["summary_sha256"], d2["complete.json"]["summary_sha256"],
                         "prepare 汇总变了 ⇒ 派生检查影响了未裁决口径下的读数")
        self.assertEqual(d1["summary.json"]["task_outcomes"], d2["summary.json"]["task_outcomes"])


class DefaultOffTests(unittest.TestCase):
    def test_flag_defaults_to_off(self):
        import freeze_candidates as F
        args = F.parse_args([])
        self.assertFalse(args.error_control_checks,
                         "默认必须关：打开会让 dev.1 的四份产物不再逐字节可复现")
        self.assertTrue(F.parse_args(["--error-control-checks"]).error_control_checks)


if __name__ == "__main__":
    unittest.main(exit=False)
