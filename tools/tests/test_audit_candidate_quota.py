"""Fictional fixtures only: draft quota audit must never authorize release."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from audit_candidate_quota import (AuditError, DEEP, DIMENSIONS, FORMAT, audit, main,
                                   read_inputs, sha, structural_features)
from loader import load_yaml, derive_tags
from scoring.model import numeric_score


def base():
    _, samples = load_yaml(ROOT / "tools/tests/negative_cases.yaml")
    return copy.deepcopy(next(s for s in samples if s["id"] == "pos_control_ok"))


def temporal():
    s = base()
    s["script"] = [
        {"session": "s1", "phase": "write", "time": "2026-01-10T10:00:00+08:00", "turns": [{"text": "Store fictional setting."}]},
        {"session": "s2", "phase": "write", "time": "2026-02-10T10:00:00+08:00", "turns": [{"text": "Update fictional setting."}]},
        {"session": "s3", "phase": "probe", "time": "2026-03-10T10:00:00+08:00", "turns": [{"text": "Use this month setting."}]},
    ]
    return s


def multistep():
    s = base()
    s["expect"]["gold_evidence"] = ["s1.t1"]
    s["anchors"] = [dict(id=f"A{i}", type="file_content", path=p, content_regex="fictional",
                         expect="present", severity="hard", on_violation="omitted")
                    for i, p in enumerate(("a.json", "b.json"), 1)]
    return s


class StructuralTests(unittest.TestCase):
    def test_time_positive(self):
        self.assertTrue(structural_features(temporal())["deepening_time"])

    def test_time_date_only_refused(self):
        s = temporal(); s["script"][0]["time"] = "2026-01-10"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_time_timezone_required(self):
        s = temporal(); s["script"][0]["time"] = "2026-01-10T10:00:00"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_time_invalid_date(self):
        s = temporal(); s["script"][0]["time"] = "2026-01-99T10:00:00Z"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_time_inversion(self):
        s = temporal(); s["script"][1]["time"] = "2026-04-10T10:00:00Z"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_time_short_span(self):
        s = temporal(); s["script"][0]["time"] = "2026-02-01T10:00:00Z"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_time_missing_update_proxy(self):
        s = temporal(); s["script"][1]["phase"] = "disturb"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_time_relative_in_write_does_not_count(self):
        s = temporal(); s["script"][0]["turns"][0]["text"] = "this month"
        s["script"][2]["turns"][0]["text"] = "create result"
        self.assertFalse(structural_features(s)["deepening_time"])

    def test_author_tags_ignored(self):
        s = base(); before = structural_features(s)
        s["tags"] = list(DEEP)
        self.assertEqual(structural_features(s), before)
        self.assertNotIn("deepening_time", derive_tags(s))

    def disturbance(self, count):
        s = base()
        s["script"] = [s["script"][0]] + [
            {"session": f"s{i+2}", "phase": "disturb", "turns": [{"text": "same topic"}]}
            for i in range(count)] + [s["script"][-1]]
        s["script"][-1]["session"] = f"s{count+2}"
        return s

    def test_three_sessions_structural_only(self):
        s = self.disturbance(3)
        self.assertTrue(structural_features(s)["deepening_disturbance"])
        result = audit([s], {s["id"]: "deepening_disturbance"}, {})
        self.assertIsNone(result["official_deepening_gate"])
        self.assertFalse(result["release_authorized"])

    def test_three_turns_not_sessions(self):
        s = self.disturbance(1)
        s["script"][1]["turns"] *= 3
        self.assertFalse(structural_features(s)["deepening_disturbance"])

    def test_disturbance_after_probe_not_counted(self):
        s = self.disturbance(3); s["script"] = [s["script"][0], s["script"][-1]] + s["script"][1:-1]
        self.assertFalse(structural_features(s)["deepening_disturbance"])

    def test_last_write_pair_must_qualify(self):
        s = self.disturbance(3); s["script"].insert(-1, copy.deepcopy(s["script"][0]))
        self.assertFalse(structural_features(s)["deepening_disturbance"])

    def test_multistep_two_files(self):
        self.assertTrue(structural_features(multistep())["deepening_multistep"])

    def test_multistep_same_file_different_fields(self):
        s = multistep(); s["anchors"][1]["path"] = "a.json"
        s["anchors"][1]["field_path"] = "other"
        self.assertFalse(structural_features(s)["deepening_multistep"])

    def test_multistep_alias_not_counted(self):
        for path in ("A.JSON", "./a.json", "x/../a.json", "C:/b.json", "a\\b.json"):
            with self.subTest(path=path):
                s = multistep(); s["anchors"][1]["path"] = path
                self.assertFalse(structural_features(s)["deepening_multistep"])

    def test_multistep_absent_soft_fixture_not_counted(self):
        for change in ({"expect": "absent"}, {"severity": "soft"}, {"must_equal_fixture": True}, {"content_regex": ""}):
            with self.subTest(change=change):
                s = multistep(); s["anchors"][1].update(change)
                self.assertFalse(structural_features(s)["deepening_multistep"])

    def test_multistep_history_required(self):
        s = multistep(); s["expect"].pop("gold_evidence")
        self.assertFalse(structural_features(s)["deepening_multistep"])

    def test_withhold_overlap_is_visible(self):
        s = base(); s["dimension"] = "boundary_recognition"; s["expect"]["boundary_kind"] = "withhold"
        r = audit([s], {s["id"]: "core"}, {})["literal_symmetry"]
        self.assertEqual((r["raw_positive"], r["raw_brake"], r["overlap"]), (1, 1, 1))
        self.assertEqual(r["withhold_priority_diagnostic"]["positive"], 0)

    def test_negative_anchor_does_not_make_brake(self):
        s = multistep(); s["anchors"][0]["expect"] = "absent"
        r = audit([s], {s["id"]: "core"}, {})
        self.assertEqual(r["literal_symmetry"]["raw_brake"], 0)

    def test_relabeling_success_would_score_zero(self):
        for v in ("omitted", "confused", "wrongly_persisted", "wrongly_reused"):
            self.assertEqual(numeric_score(v), 0)
        self.assertEqual(numeric_score("remembered"), 1)

    def test_actual_rule_path_uses_failure_label_after_all_anchors_pass(self):
        from unittest.mock import MagicMock, patch
        from scoring.decide import decide_task
        from scoring.model import AnchorOutcome
        idx = MagicMock()
        idx.system_action.side_effect = lambda task, action: ({"id": "ev_summary", "payload": {"status": "ok"}}
                                                             if action == "task_summary" else None)
        idx.task_rows.return_value = [{"id": "ev_summary"}]
        idx.total_turns.return_value = 1
        idx.agent_messages.return_value = [{}]
        outcome = AnchorOutcome("A1", "file_content", "hard", "pass", "present", "omitted")
        for verdict in ("remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"):
            s = multistep(); s["anchors"] = s["anchors"][:1]; s["expect"]["verdict"] = verdict
            with patch("scoring.decide.evaluate", return_value=outcome):
                result = decide_task(s, idx, "fictional_task")
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.verdict, verdict)
            self.assertEqual(result.score, 1.0 if verdict == "remembered" else 0.0)

    def test_deepening_not_also_core(self):
        s = multistep(); r = audit([s], {s["id"]: "deepening_multistep"}, {})
        self.assertEqual(sum(r["core_counts"].values()), 0)
        self.assertEqual(r["deepening_declared"]["deepening_multistep"], 1)

    def test_100_and_all_structural_checks_never_release(self):
        samples, slots = [], {}
        for d in DIMENSIONS:
            for i in range(15):
                s = base(); s["id"] = f"fiction_{len(samples)}"; s["dimension"] = d
                if d == "boundary_recognition":
                    s["expect"]["boundary_kind"] = "withhold" if i < 10 else "release"
                samples.append(s); slots[s["id"]] = "core"
        for slot, count in DEEP.items():
            for _ in range(count):
                s = temporal() if slot == "deepening_time" else self.disturbance(3) if slot == "deepening_disturbance" else multistep()
                s["id"] = f"fiction_{len(samples)}"; samples.append(s); slots[s["id"]] = slot
        r = audit(samples, slots, {})
        self.assertTrue(all(r["quota_diagnostics_not_release_gates"].values()))
        self.assertFalse(r["release_authorized"])
        self.assertEqual(r["formal_quota_accepted"], 0)
        self.assertIsNone(r["official_symmetry_gate"])


class InputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paper, self.slots, self.out = [self.root / n for n in ("paper.jsonl", "slots.json", "report.json")]
        self.s = base()
        self.paper.write_text(json.dumps(self.s) + "\n", encoding="utf8")
        self.m = {"format": FORMAT, "paper_sha256": sha(self.paper.read_bytes()),
                  "rows": [{"sample_id": self.s["id"], "slot": "core"}]}
        self.save()

    def save(self):
        self.slots.write_text(json.dumps(self.m), encoding="utf8")

    def cli(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--paper", str(self.paper), "--slots", str(self.slots), "--out", str(self.out)])

    def test_cli_aggregate_only_and_read_only(self):
        original = (self.paper.read_bytes(), self.slots.read_bytes())
        self.assertEqual(self.cli(), 0)
        data = self.out.read_text(encoding="utf8")
        self.assertNotIn(self.s["id"], data)
        for x in self.s["script"]:
            for t in x["turns"]:
                self.assertNotIn(t["text"], data)
        self.assertEqual(original, (self.paper.read_bytes(), self.slots.read_bytes()))

    def test_existing_output_not_overwritten(self):
        self.out.write_text("preserve")
        self.assertEqual(self.cli(), 2)
        self.assertEqual(self.out.read_text(), "preserve")

    def test_output_cannot_replace_paper(self):
        before = self.paper.read_bytes(); self.out = self.paper
        self.assertEqual(self.cli(), 2); self.assertEqual(before, self.paper.read_bytes())

    def test_wrong_hash(self):
        self.m["paper_sha256"] = "0" * 64; self.save()
        self.assertEqual(self.cli(), 2); self.assertFalse(self.out.exists())

    def test_missing_extra_duplicate_rows(self):
        row = self.m["rows"][0]
        for rows in ([], [row, row], [row, {"sample_id": "extra_id", "slot": "core"}]):
            self.m["rows"] = rows; self.save()
            self.assertEqual(self.cli(), 2); self.assertFalse(self.out.exists())

    def test_duplicate_paper_ids(self):
        self.paper.write_bytes(self.paper.read_bytes() * 2)
        self.m["paper_sha256"] = sha(self.paper.read_bytes()); self.save()
        self.assertEqual(self.cli(), 2)

    def test_bad_slot_type_or_name(self):
        for slot in ("made_up", ["core"], True):
            self.m["rows"][0]["slot"] = slot; self.save()
            self.assertEqual(self.cli(), 2)

    def test_approval_field_cannot_be_smuggled_in(self):
        self.m["release_authorized"] = True; self.save()
        self.assertEqual(self.cli(), 2)

    def test_duplicate_json_key(self):
        self.slots.write_text('{"format": "x", "format": "y"}')
        self.assertEqual(self.cli(), 2)

    def test_empty_and_invalid_paper(self):
        for text in ("", "[]", "{broken", '{"id":"private_secret"}'):
            self.paper.write_text(text)
            self.assertEqual(self.cli(), 2)

    def test_schema_invalid_does_not_leak(self):
        self.paper.write_text('{"id":"private_secret"}')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["--paper", str(self.paper), "--slots", str(self.slots), "--out", str(self.out)]), 2)
        self.assertNotIn("private_secret", err.getvalue())

    def test_input_changed_before_write(self):
        from unittest.mock import patch
        original = audit
        def mutate(*args):
            self.paper.write_bytes(self.paper.read_bytes() + b"\n")
            return original(*args)
        with patch("audit_candidate_quota.audit", side_effect=mutate):
            self.assertEqual(self.cli(), 2)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
