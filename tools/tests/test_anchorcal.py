#!/usr/bin/env python3
"""anchorcal（红线4 锚点可执行性校准）的守卫。

这套用例里有一半是**我自己第一版工具的冤案回归钉**：
  ① 第一版把"在全量真实语料上零命中"一律报成死锚，于是 `retain_allergy_001:A1`（`value_regex: 花生`）
     被指控 —— 而"花生"在真实回复里出现过 12 次，只是该题的记忆面没采到（hermes 探针整轮未证实）。
     修法是 memory_entry 的可实现性必须用同一个真实匹配器 hit_entries 扫**全量**语料。
  ② 第一版对"该题不在真实批里"的 memory/reply 锚点仍走可实现性判定，于是
     `retain_pet_name_001:A1`（`芝麻`）被指控 —— 语料里没有它的值，只因为这道题从没跑过。
  ③ 第一版只认 file_write 事件，于是 `reuse_report_001:A3`（钉 fixture `周报模板.md`）被判
     path_never_produced —— fixture 是题面自带的初始文件，永远不会出现在 file_write 里，
     但真实工作区清单里明明有它。修法是按证据强度分层：真实写入 > 题面 fixture > 工作区清单。
三条都在真实凭据上钉死（见 RealCredentialTests），也在合成 run 上钉死（见 CalibrationTests）——
合成那半是为了在没有 runs/ 的机器上（VM 同步范围不含 runs/）这套口径依然被守住。
"""
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

import testtmp                                                  # noqa: E402
import anchorcal as AC                                          # noqa: E402
from scoring.index import EvidenceIndex                         # noqa: E402

REAL_RUN = ROOT / "runs/run_v02_first"
CRED = ROOT / "notes/anchorcal-paper-r1.json"
PAPER = ROOT / "build/paper-r1.jsonl"


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


def row(rid, task, rtype, source, actor, payload, raw_ref=None, session=None, turn=None):
    return {"id": rid, "run_id": "anchorcal_synthetic", "seq": 0, "ts": "2026-10-08T00:00:00+08:00",
            "task_id": task, "session_id": session, "turn": turn, "source": source, "type": rtype,
            "actor": actor, "payload": payload, "raw_ref": raw_ref or {}, "pii_hits": []}


def build_synthetic_run(root: pathlib.Path) -> EvidenceIndex:
    """一个最小但**形状真实**的 run：两个 Agent（其一记忆探针未证实）、两个样本、
    回复/产物/记忆快照/工作区清单四类证据齐备。"""
    (root / "raw").mkdir(parents=True, exist_ok=True)
    tasks = ["agA-smp_one", "agB-smp_one", "agA-smp_two"]
    rows = []
    # agA-smp_one：回复含"花生/芝麻"，写了 周报.md，记忆面有"芝麻"，清单里还有 fixture 周报模板.md
    rows.append(row("e1", "agA-smp_one", "message", "dialogue", "agent",
                    {"text": "记下了：花生过敏，芝麻可以。"}))
    rows.append(row("e2", "agA-smp_one", "file_write", "file", "agent",
                    {"path": "周报.md", "op": "create", "after_content": "# 亮点\n# 量化\n# 拦路虎\n"}))
    (root / "raw" / "mem_a1.jsonl").write_text(
        json.dumps({"key": "饮食", "value": "芝麻可以，不吃辣"}, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    rows.append(row("e3", "agA-smp_one", "memory_read", "memory", "env",
                    {"phase": "after", "via": "probe", "entries_count": 1,
                     "coverage": {"entries_scanned": 1}},
                    raw_ref={"path": "raw/mem_a1.jsonl", "line": 1}))
    (root / "raw" / "list_a1.jsonl").write_text(
        "".join(json.dumps({"path": p, "sha256": "0" * 64, "size": 1, "mtime_ns": 1}) + "\n"
                for p in ("周报.md", "周报模板.md")), encoding="utf-8", newline="\n")
    rows.append(row("e4", "agA-smp_one", "message", "system", "env",
                    {"action": "workspace_listing", "ok": True},
                    raw_ref={"path": "raw/list_a1.jsonl", "line": 1}))
    # agB-smp_one：记忆探针扫到 0 条 ⇒ agB 整轮未证实（mem_probe_unverified）
    rows.append(row("e5", "agB-smp_one", "message", "dialogue", "agent", {"text": "好的。"}))
    (root / "raw" / "mem_b1.jsonl").write_text("", encoding="utf-8", newline="\n")
    rows.append(row("e6", "agB-smp_one", "memory_read", "memory", "env",
                    {"phase": "after", "via": "probe", "entries_count": 0,
                     "coverage": {"entries_scanned": 0}},
                    raw_ref={"path": "raw/mem_b1.jsonl", "line": 1}))
    # agA-smp_two：只有回复，没有产物
    rows.append(row("e7", "agA-smp_two", "message", "dialogue", "agent", {"text": "芝麻开门。"}))
    manifest = {"run_id": "anchorcal_synthetic", "schema_version": "evidence.schema.json/v0.1",
                "contract_version": "v0.1", "suite_version": "vTEST",
                "agents": [{"name": "agA", "agent_kind": "real"},
                           {"name": "agB", "agent_kind": "real"}],
                "tasks": [{"task_id": t} for t in tasks]}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                        encoding="utf-8", newline="\n")
    (root / "evidence.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8", newline="\n")
    return EvidenceIndex.load(root)


class SyntheticRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(testtmp.mkdtemp(prefix="okb-anchorcal-"))
        cls.idx = build_synthetic_run(cls.tmp)
        cls.corp = AC.Corpus(cls.idx)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def cal(self, anchor, sid="smp_one", fixture=None, min_pool=5):
        sample = {"id": sid, "dimension": "memory_recall", "fixture": fixture or []}
        return AC.calibrate(anchor, sample, self.corp, min_pool)

    # ---- 语料分层与探针口径 ----
    def test_corpus_separates_agents_and_records_probe_coverage(self):
        self.assertEqual(self.corp.probe_unverified, {"agB"},
                         "agB 的记忆快照 0 条 ⇒ 必须整轮记为未证实（读不到 ≠ 没记住）")
        self.assertEqual(sorted(self.corp.task_of_sample), ["smp_one", "smp_two"])
        self.assertIn("周报模板.md", self.corp.listing["smp_one"],
                      "工作区清单没进来 ⇒ fixture/既有文件会被冤枉成 path_never_produced")

    # ---- ① 死锚指控 ----
    def test_unrealizable_present_predicate_is_suspect_dead(self):
        r = self.cal({"id": "A1", "type": "reply_text", "turn": "s1.t1", "regex": "斑马线 quantifier",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"})
        self.assertEqual(r["status"], "suspect_dead_predicate")
        self.assertFalse(r["realizable"])

    def test_realizable_present_predicate_is_live(self):
        r = self.cal({"id": "A1", "type": "reply_text", "turn": "s1.t1", "regex": "花生",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"})
        self.assertEqual(r["status"], "live")
        self.assertTrue(r["realizable"])

    # ---- ② 该题没跑过 ⇒ 不许指控 ----
    def test_sample_not_in_run_is_undecidable_not_dead(self):
        r = self.cal({"id": "A1", "type": "reply_text", "turn": "s1.t1", "regex": "从不出现的词",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"},
                     sid="smp_never_ran")
        self.assertEqual(r["status"], "undecidable_no_matching_task")
        self.assertNotEqual(r["status"], "suspect_dead_predicate")

    def test_sample_not_in_run_file_anchor_is_undecidable_too(self):
        """file_content 一支曾经早退、跳过了可实现性计算 ⇒ 与 memory/reply 口径不一致。"""
        r = self.cal({"id": "A1", "type": "file_content", "path": "任意.md", "regex": "花生",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"},
                     sid="smp_never_ran")
        self.assertEqual(r["status"], "undecidable_no_matching_task")
        self.assertIsNotNone(r["realizable"], "file_content 早退导致可实现性没算")

    # ---- ③ fixture / 工作区清单分层 ----
    def test_fixture_path_is_not_path_never_produced(self):
        r = self.cal({"id": "A3", "type": "file_content", "path": "周报模板.md",
                      "expect": "present", "severity": "hard", "on_violation": "omitted",
                      "content_regex": "模板"},
                     fixture=[{"path": "周报模板.md", "content": "模板正文"}])
        self.assertNotEqual(r["status"], "path_never_produced",
                            "fixture 路径被冤枉成从未落盘（第一版的冤案③）")
        self.assertTrue(r["path_in_fixture"])
        self.assertEqual(r["content_provenance"], "paper_fixture（题面自带初始正文）")

    def test_listed_but_unwritable_path_is_undecidable_content(self):
        r = self.cal({"id": "A3", "type": "file_content", "path": "周报模板.md",
                      "expect": "present", "severity": "hard", "on_violation": "omitted",
                      "content_regex": "模板"})
        self.assertEqual(r["status"], "undecidable_content_unavailable",
                         "路径在真实清单里但正文不在证据中 ⇒ 判不了内容，不许当通过")

    def test_path_absent_everywhere_is_path_never_produced(self):
        r = self.cal({"id": "A1", "type": "file_content", "path": "从没出现.md",
                      "expect": "present", "severity": "hard", "on_violation": "omitted",
                      "content_regex": "亮点"})
        self.assertEqual(r["status"], "path_never_produced")
        self.assertIn("周报.md", r["detail"], "必须把真实出现过的路径写进理由，否则不可核")

    def test_written_path_uses_real_content(self):
        r = self.cal({"id": "A2a", "type": "file_content", "path": "周报.md",
                      "expect": "present", "severity": "hard", "on_violation": "omitted",
                      "content_regex": "拦路虎"})
        self.assertEqual(r["status"], "live")
        self.assertEqual(r["content_provenance"], "real_file_write")

    # ---- 记忆面：可实现性 vs 证据到位性 ----
    def test_memory_predicate_realizable_but_absent_from_own_memory(self):
        """`花生` 在真实回复里出现、但记忆面没有 ⇒ 是采集/行为问题，不是死锚（冤案①）。"""
        r = self.cal({"id": "A1", "type": "memory_entry", "key": "*", "value_regex": "花生",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"})
        self.assertTrue(r["realizable"], "全量语料里有『花生』，必须判可实现")
        self.assertEqual(r["status"], "evidence_absent_not_predicate")

    def test_memory_evidence_with_unverified_probe_is_not_an_accusation(self):
        """记忆面读数不可判时不许指控锚点（CONTRACT §5：读不到 ≠ 没记住）。

        这个用例曾经把共享夹具的 `corp.entries` 清空后没还原，污染了同类后续 8 个用例
        （语料池从 6 条掉到 4 条 ⇒ 全体误判 undecidable_small_pool）。共享夹具 + 半吊子
        teardown 就是这么把一套绿测试变成一片假红的；现在存/还原都显式做，并且断言还原成功。
        """
        corp = self.corp
        tasks = list(corp.task_of_sample["smp_one"])
        saved_tasks, saved_entries = list(tasks), list(corp.entries["smp_one"])
        self.assertTrue(any(corp.entry_probe_ok[t] for t in tasks)
                        and any(not corp.entry_probe_ok[t] for t in tasks),
                        "夹具前提：smp_one 同时要有已证实与未证实探针的 Agent")
        try:
            # 只留未证实探针的那个 Agent 的任务、且它的记忆面为空 ⇒ 必须记不可判而不是死锚
            corp.task_of_sample["smp_one"] = [t for t in tasks if not corp.entry_probe_ok[t]]
            corp.entries["smp_one"] = []
            # min_pool=1：本用例会临时清空记忆条目（全量语料 6→4 条），
            # 不隔离就会先撞上 undecidable_small_pool，测不到探针分支
            r = self.cal({"id": "A1", "type": "memory_entry", "key": "*", "value_regex": "花生",
                          "expect": "present", "severity": "hard", "on_violation": "omitted"},
                         min_pool=1)
            self.assertEqual(r["status"], "evidence_absent_probe_unverified")
        finally:
            corp.task_of_sample["smp_one"] = saved_tasks
            corp.entries["smp_one"] = saved_entries
        self.assertEqual(len(corp.all_texts), 6, "夹具没还原干净，后面的用例会读到被污染的语料池")

    def test_memory_hit_is_live(self):
        r = self.cal({"id": "A1", "type": "memory_entry", "key": "*", "value_regex": "芝麻",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"})
        self.assertEqual(r["status"], "live")

    # ---- 极性与池大小 ----
    def test_absent_unrealizable_is_unverified_negative(self):
        r = self.cal({"id": "A3", "type": "reply_text", "turn": "s1.t1", "regex": "永不出现短语",
                      "expect": "absent", "severity": "soft", "on_violation": "wrongly_persisted"})
        self.assertEqual(r["status"], "unverified_negative")

    def test_tautological_predicate_is_flagged_not_counted_live(self):
        r = self.cal({"id": "A1", "type": "reply_text", "turn": "s1.t1", "regex": ".*",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"})
        self.assertEqual(r["status"], "matches_everything",
                         "恒真谓词没有区分力，混进 live 就是拿结构锚点充当有效判据")

    def test_small_corpus_is_undecidable_not_pass(self):
        r = self.cal({"id": "A1", "type": "reply_text", "turn": "s1.t1", "regex": "花生",
                      "expect": "present", "severity": "hard", "on_violation": "omitted"},
                     min_pool=100000)
        self.assertEqual(r["status"], "undecidable_small_pool")

    def test_event_anchors_are_not_calibrated_by_snapshots(self):
        r = self.cal({"id": "A1", "type": "memory_write", "expect": "absent",
                      "severity": "hard", "on_violation": "wrongly_persisted", "key_regex": ".*"})
        self.assertEqual(r["status"], "not_calibratable_event_anchor")


@unittest.skipUnless(REAL_RUN.is_dir() and CRED.is_file() and PAPER.is_file(),
                    "缺真实批 runs/run_v02_first 或凭据（不在 VM 同步范围内）")
class RealCredentialTests(unittest.TestCase):
    """在真凭据上钉死三条冤案不得复发，并核对凭据自洽 + 可复算。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(CRED.read_text(encoding="utf-8"))
        cls.pa = {(r["sample_id"], r["anchor_id"]): r for r in cls.doc["per_anchor"]}

    def test_check_mode_reproduces(self):
        proc = subprocess.run([sys.executable, "tools/anchorcal.py",
                               "--out", "notes/anchorcal-paper-r1.json", "--check"],
                              cwd=str(ROOT), capture_output=True, encoding="utf-8",
                              errors="replace", timeout=600, env=env_with_deps())
        self.assertEqual(proc.returncode, 0, (proc.stdout or "") + (proc.stderr or ""))

    def test_credential_is_bound_to_the_delivery_paper(self):
        import hashlib
        self.assertEqual(self.doc["paper_sha256"],
                         hashlib.sha256(PAPER.read_bytes()).hexdigest(),
                         "凭据量的不是这份卷 ⇒ 换卷必须重跑 okb anchorcal")

    def test_histogram_and_coverage_are_self_consistent(self):
        h = self.doc["status_histogram"]
        self.assertEqual(sum(h.values()), self.doc["anchors_total"])
        self.assertEqual(self.doc["decidable_anchors"] + self.doc["undecidable_total"]
                         + self.doc["status_histogram"].get("not_calibratable_event_anchor", 0),
                         self.doc["anchors_total"],
                         "可判 + 不可判 + 事件类 必须等于锚点总数（不许有锚点没被归类）")
        self.assertEqual(self.doc["anchors_total"],
                         sum(len(r["anchors"]) for r in
                             [json.loads(l) for l in PAPER.read_text(encoding="utf-8").splitlines()
                              if l.strip()]))

    def test_regression_no_false_dead_anchor_on_memory_face(self):
        """冤案①：`花生` 在真实回复里出现过，只因记忆面没采到就被指控死锚。"""
        r = self.pa[("retain_allergy_001", "A1")]
        self.assertNotEqual(r["status"], "suspect_dead_predicate")
        self.assertTrue(r["realizable"], "全量真实语料里确实有『花生』")

    def test_regression_uncovered_sample_is_never_accused(self):
        """冤案②：题目没跑过 ⇒ 语料里没有它自己的值是预期的，不构成死锚指控。"""
        r = self.pa[("retain_pet_name_001", "A1")]
        self.assertEqual(r["status"], "undecidable_no_matching_task")
        self.assertEqual(self.doc["unrealizable_in_global_corpus"]["covered_by_run"], [],
                         "跑过的题里出现零命中谓词才是死锚指控，必须为空才可判红线4")

    def test_regression_fixture_path_is_not_path_never_produced(self):
        """冤案③：`周报模板.md` 是题面 fixture，只在 file_write 里找它就等于冤枉它。"""
        r = self.pa[("reuse_report_001", "A3")]
        self.assertNotEqual(r["status"], "path_never_produced")
        self.assertTrue(r["path_in_fixture"] or r["path_in_workspace_listing"],
                        "fixture/工作区清单两条证据都没认出来")

    def test_genuine_findings_are_still_reported(self):
        """反过来钉：真发现不许被"少报以求绿"抹掉（真实批里 Agent 落盘叫 周报-2026-09-25.md）。"""
        names = {(r["sample_id"], r["anchor_id"]) for r in self.doc["path_never_produced"]}
        self.assertIn(("reuse_report_001", "A1"), names)
        self.assertIn(("boundary_temp_001", "A1"), names)
        self.assertGreaterEqual(len(names), 5, "真发现被压掉了？现场重算 %s" % sorted(names))
        self.assertEqual(self.doc["suspect_dead_predicate"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
