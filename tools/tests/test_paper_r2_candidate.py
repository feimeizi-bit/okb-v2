#!/usr/bin/env python3
"""r2 候选卷（基线/准入题族）的三条不变量：能编译、作用域真的分开、以及 r1 绝不能被改动。

背景：paper-r1 的检查项 scope 实测 memory_after 93 / agent_reply 82 / file_final 69 / memory_before 0。
缺基线断言时，"这轮没记住"与"这条从来没被写过"塌成同一个 omitted（诊断意义相反）。
r2 用题卡 schema 已有的 `evaluate_at: snapshot_before` 补这一类，**不动契约**。
"""
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

R1_YAML_SHA = "f30541ae271690a5d07511a5260a22386224bcadd753e075da40cf788ff5cc60"
R1_JSONL_SHA = "0da837550437684b3ac8eec7c415faa5c0b65d0629cf777ad3d1be3d9d1f7867"
R1_PLAN_SHA = "5bed443dabb46be33a84cbff19d490c1c7d6832654bf8d855e99fa1b6329db97"


def sha(p: pathlib.Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


class PaperR2CandidateTests(unittest.TestCase):
    def test_r1_artifacts_are_byte_identical(self):
        """r2 只增不改：交付卷三份指纹必须逐字不变（这是候选卷存在的前提条件）。"""
        for rel, want in (("samples/paper-r1.yaml", R1_YAML_SHA),
                          ("build/paper-r1.jsonl", R1_JSONL_SHA),
                          ("samples/four-layer-r1.json", R1_PLAN_SHA)):
            p = ROOT / rel
            if not p.is_file():
                self.skipTest("%s 未生成（干净环境先跑 build_paper/freeze）" % rel)
            self.assertEqual(sha(p), want, "r1 产物被改动：" + rel)

    def test_derive_honours_evaluate_at_snapshot_before(self):
        """回归：派生器曾把 memory_entry 一律映射成 memory_after，导致基线类检查项无法进四层。"""
        from freeze_candidates import derive_pilot_checks
        sample = dict(id="probe_baseline_001",
                      anchors=[dict(id="A1", type="memory_entry", key="*", expect="present",
                                    severity="hard", on_violation="omitted", desc="正向结构锚点"),
                               dict(id="A3", type="memory_entry", key="*", value_regex="残留项",
                                    expect="absent", evaluate_at="snapshot_before", severity="hard",
                                    on_violation="wrongly_persisted", desc="基线污染判定")])
        checks, gaps, disp = derive_pilot_checks(sample)
        # 检查项 id 的真实格式是 "pilot_<sample_id>_<anchor_id>"（derive_pilot_checks 决定）
        by = {c["id"]: c for c in checks}
        self.assertEqual(sorted(by), ["pilot_probe_baseline_001_A1", "pilot_probe_baseline_001_A3"])
        self.assertEqual(by["pilot_probe_baseline_001_A1"]["scope"], "memory_after")
        self.assertEqual(by["pilot_probe_baseline_001_A3"]["scope"], "memory_before")
        self.assertIn("evaluate_at=snapshot_before", by["pilot_probe_baseline_001_A3"]["basis"])
        self.assertEqual(gaps, [])
        self.assertEqual(disp["A3"]["role"], "semantic")

    def test_before_and_after_scopes_read_different_observations(self):
        """作用域真的分开：同一个 digest 下 memory_before/memory_after 必须落在不同证据对象上。"""
        from scoring.four_layer import observations

        def ev(key, phase, entries):
            return dict(evidence_key=key, type="memory_read", actor="env",
                        payload=dict(phase=phase, entries=entries))

        digest = dict(events=[ev("E1", "before", [{"k": "旧值"}]),
                              ev("E6", "after", [{"k": "新值"}])])
        before = observations(digest, {"scope": "memory_before"})
        after = observations(digest, {"scope": "memory_after"})
        self.assertEqual([o["evidence_id"] for o in before], ["E1"])
        self.assertEqual([o["evidence_id"] for o in after], ["E6"])
        self.assertNotEqual(before[0]["evidence_id"], after[0]["evidence_id"])
        self.assertEqual(observations(digest, {"scope": "memory_before"})[0]["content"]["phase"],
                         "before")

    def test_extra_cards_still_compile_against_the_real_schema(self):
        """题卡必须过 loader/schema：本轮就在这里踩过 pii 形状、boundary_kind、key="*"、
        suite_version 形状四类错——留成测试，别再靠人记得。"""
        out = ROOT / "build" / "_test_paper_r2_extra.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        proc = subprocess.run([sys.executable, str(ROOT / "tools/build_paper.py"),
                               "samples/paper-r2-extra.yaml", "-o", str(out)],
                              cwd=str(ROOT), capture_output=True, encoding="utf-8",
                              errors="replace", timeout=300)
        try:
            self.assertEqual(proc.returncode, 0, proc.stdout[-1200:] + proc.stderr[-600:])
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(len(rows), 4)
            for r in rows:
                a3 = [a for a in r["anchors"] if a["id"] == "A3"][0]
                self.assertEqual(a3["evaluate_at"], "snapshot_before")
                self.assertEqual(a3["expect"], "absent")
                self.assertIn(a3["on_violation"],
                              ("wrongly_persisted", "wrongly_reused", "confused", "omitted"))
        finally:
            if out.exists():
                out.unlink()

    def test_r2_plan_validates_and_adds_memory_before(self):
        """r2 计划必须过评分侧自己的 validate_plan，且基线检查项真的进去了。"""
        plan_p = ROOT / "samples/four-layer-r2.json"
        jsonl_p = ROOT / "build/paper-r2.jsonl"
        if not (plan_p.is_file() and jsonl_p.is_file()):
            self.skipTest("r2 未生成：先跑 python3 tools/extend_paper_r2.py")
        from scoring.four_layer import validate_plan
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        rows = [json.loads(l) for l in jsonl_p.read_text(encoding="utf-8").splitlines() if l.strip()]
        validate_plan(plan, rows, sha(jsonl_p))
        scopes = [c.get("scope") for s in plan["samples"] for c in s["checks"]]
        self.assertGreaterEqual(scopes.count("memory_before"), 4, "基线检查项少于 4 条")
        self.assertEqual(len(plan["samples"]), 104)
        # 前 100 行必须与 r1 计划逐字相同（只增不改）
        r1 = json.loads((ROOT / "samples/four-layer-r1.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["samples"][:len(r1["samples"])], r1["samples"],
                         "r2 改写了 r1 的计划行——候选卷不允许")


if __name__ == "__main__":
    unittest.main(exit=False)
