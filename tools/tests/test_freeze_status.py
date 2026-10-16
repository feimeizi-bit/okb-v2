#!/usr/bin/env python3
"""`okb freeze-status` 的可复现性与可失败性。

这张报告器是 F1-F7 回执的数字来源，所以它自己必须先被守住：
① 凭据 == 现场重算（改了题/改了红线口径就必须重跑）；
② 数字来自磁盘，不来自记忆（配额/混淆判据/对称集都用独立实现重算一遍对撞）；
③ 放宽口径不会被悄悄塞进脚本（红线2 必须同时报字面读法与宽松读法）。
"""
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import freeze_status as fs  # noqa: E402

CRED = ROOT / "notes/freeze-status-v1.0.json"
PAPER = ROOT / "build/paper-r1.jsonl"


class FreezeStatusTests(unittest.TestCase):
    def setUp(self):
        if not PAPER.is_file():
            self.skipTest("交付卷未编译（干净环境跳过，不假红）")

    def test_credential_matches_recomputation(self):
        p = subprocess.run([sys.executable, str(ROOT / "tools/freeze_status.py"), "--check"],
                           cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout or '') + (p.stderr or ''))

    def test_numbers_match_an_independent_recount(self):
        rows = [json.loads(l) for l in PAPER.read_text(encoding="utf-8").splitlines() if l.strip()]
        doc = json.loads(CRED.read_text(encoding="utf-8"))
        dims = {}
        for r in rows:
            d = r["dimension"]
            a = r.get("anchors") or []
            g = dims.setdefault(d, {"hard_confused": 0, "absent_prod_samples": 0, "items": 0})
            g["items"] += 1
            g["hard_confused"] += sum(1 for x in a if x.get("severity") == "hard"
                                      and x.get("on_violation") == "confused")
            if any(x.get("expect") == "absent" and x.get("type") == "file_content" for x in a):
                g["absent_prod_samples"] += 1
        for d, got in doc["red_line_1_confusion"]["per_dimension"].items():
            want = dims[d]["hard_confused"] + dims[d]["absent_prod_samples"]
            self.assertEqual(got["confusion_judgements"], want, "红线1 计数漂移：" + d)
            self.assertEqual(got["items"], dims[d]["items"], "配额计数漂移：" + d)
        self.assertEqual(sum(dims[d]["items"] for d in dims), len(rows))

    def test_symmetry_reports_both_readings(self):
        """读法不同结论就不同——只报一种等于挑好看的。"""
        doc = json.loads(CRED.read_text(encoding="utf-8"))
        s2 = doc["red_line_2_symmetry"]
        for key in ("gap_literal", "gap_lenient", "allowed_gap", "ok_literal", "ok_lenient"):
            self.assertIn(key, s2, key)
        self.assertGreater(s2["gap_lenient"], s2["allowed_gap"], "宽松读法当前也必须是不达标")

    def test_reporter_can_fail_on_tampered_counts(self):
        """可失败性：把 expect.verdict 全改成刹车，对称集读法就要变（否则这行数字是装饰）。"""
        rows = [json.loads(l) for l in PAPER.read_text(encoding="utf-8").splitlines() if l.strip()][:40]
        base = fs.measure(rows)
        tampered = json.loads(json.dumps(rows))
        for r in tampered:
            r.setdefault("expect", {})["verdict"] = "omitted"
            r["tags"] = (r.get("tags") or []) + ["has_time_anchor"]
        got = fs.measure(tampered)
        # 全部刷成刹车后 |正-刹| 的**数值**可以不变（40 vs 40），但两侧归属必须翻转：
        # 上一版我拿 gap 当被测量，用例于是变成"永远绿"——被测量选错，可失败性就是假的。
        self.assertEqual(got["red_line_2_symmetry"]["by_verdict"]["positive"], 0)
        self.assertEqual(got["red_line_2_symmetry"]["by_verdict"]["brake"], len(tampered))
        self.assertNotEqual(base["red_line_2_symmetry"]["by_verdict"],
                             got["red_line_2_symmetry"]["by_verdict"])
        self.assertGreater(got["deep_slots"]["时间锚定"]["measured"],
                           base["deep_slots"]["时间锚定"]["measured"])

    def test_missing_paper_is_refused_not_guessed(self):
        p = subprocess.run([sys.executable, str(ROOT / "tools/freeze_status.py"),
                            "--paper", "build/does-not-exist.jsonl",
                            "--out", "build/never-written.json"],
                           cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(p.returncode, 2, (p.stdout or '') + (p.stderr or ''))
        self.assertIn("不猜读数", p.stderr)
        self.assertFalse((ROOT / "build/never-written.json").is_file(), "失败时不得留下半份凭据")


if __name__ == "__main__":
    unittest.main(exit=False)
