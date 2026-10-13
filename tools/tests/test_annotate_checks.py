"""检查项标注回路的机器测试：盲题、拒重复、闭合前不出判官答案、κ 与精确下界。

全部在临时目录里用**合成包**跑：这里的 "rater" 标签是程序造的机器输入，用来验证工具
会不会放行错误输入，**不是人类标签，也不是任何成绩**。真实的真人标签只能由两个人各自
填进 worklist 之后产生。
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
import annotate_checks as A                                      # noqa: E402

REQ_SHA = "ab" * 32


def synth_package(dir_, decided=("supported", "contradicted", "supported", "insufficient")):
    results, requests = [], []
    for i, verdict in enumerate(decided):
        tid, cid = "agent-a%d" % i, "check%d" % i
        requirement = "Requirement number %d about the user's stated preference." % i
        observations = [dict(evidence_id="E1", content={"value": "some observed memory text"})]
        request = dict(requirement=requirement, scope="memory_after",
                       context=dict(scope={"scope": "memory_after"}), observations=observations)
        results.append(dict(task_id=tid, sample_id="s%d" % i, layer2=[],
                            layer3=[dict(id=cid, layer="semantic", on_failure="omitted",
                                         result=verdict,
                                         **({"rationale": "judge said so", "citations":
                                            [dict(evidence_id="E1", pointer="/value",
                                                  quote="some observed memory text")]}
                                            if verdict != "insufficient" else {}))],
                            semantic_requests=[dict(check_id=cid, request_sha256=REQ_SHA,
                                                    request=request)]))
        requests.append(dict(task_id=tid, check_id=cid, request_sha256=REQ_SHA, request=request))
    (dir_ / "results.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8",
                                        newline="\n")
    (dir_ / "semantic-requests.json").write_text(json.dumps(requests, ensure_ascii=False),
                                                  encoding="utf-8", newline="\n")
    (dir_ / "summary.json").write_text(json.dumps(dict(evidence_policy="scoped-v2",
                                                       mode="replay")), encoding="utf-8", newline="\n")


def labels_from(pairs):
    return "".join(json.dumps(dict(row_key=k, label=l, note="reason text long enough "
                                             "for %s" % k), ensure_ascii=False) + "\n"
                   for k, l in pairs)


class AnnotateChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.pkg = self.root / "score"
        self.pkg.mkdir(parents=True)
        synth_package(self.pkg)
        self.pub = self.root / "public"
        self.assertEqual(A.main(["make-worklist", "--score-dir", str(self.pkg),
                                 "--out-dir", str(self.pub), "--decided-only"]), 0)
        self.worklist = self.pub / "worklist.jsonl"

    def test_public_worklist_hides_every_judge_answer(self):
        blob = self.worklist.read_text(encoding="utf-8")
        for field in A.ANSWER_FIELDS:
            self.assertNotIn('"%s"' % field, blob, field)
        self.assertNotIn("judge said so", blob)
        self.assertEqual(len(A.read_jsonl(self.worklist)), 5)     # manifest + 4 rows

    def test_decided_only_still_samples_unanswered_rows_when_off(self):
        out = self.root / "all"
        self.assertEqual(A.main(["make-worklist", "--score-dir", str(self.pkg),
                                 "--out-dir", str(out)]), 0)
        self.assertEqual(len(A.read_jsonl(out / "worklist.jsonl")) - 1, 4)

    def test_stratification_is_deterministic_and_seeded(self):
        out_a, out_b = self.root / "s1", self.root / "s2"
        A.main(["make-worklist", "--score-dir", str(self.pkg), "--out-dir", str(out_a), "--count", "2"])
        A.main(["make-worklist", "--score-dir", str(self.pkg), "--out-dir", str(out_b), "--count", "2"])
        self.assertEqual((out_a / "worklist.jsonl").read_bytes(),
                          (out_b / "worklist.jsonl").read_bytes())

    def test_validate_rejects_duplicate_row_and_short_note_and_bad_label(self):
        keys = [i["row_key"] for i in A.read_jsonl(self.worklist)[1:]]
        good = labels_from([(k, "supported") for k in keys])
        p = self.root / "ok.jsonl"
        p.write_text(good, encoding="utf-8", newline="\n")
        self.assertEqual(A.main(["validate", "--worklist", str(self.worklist),
                                 "--labels", str(p)]), 0)
        dup = self.root / "dup.jsonl"
        dup.write_text(good + good.splitlines(True)[1], encoding="utf-8", newline="\n")
        self.assertEqual(A.main(["validate", "--worklist", str(self.worklist),
                                 "--labels", str(dup)]), 2)
        short = self.root / "short.jsonl"
        short.write_text("".join(json.dumps(dict(row_key=k, label="supported", note="x"),
                                            ensure_ascii=False) + "\n" for k in keys),
                          encoding="utf-8", newline="\n")
        self.assertEqual(A.main(["validate", "--worklist", str(self.worklist),
                                 "--labels", str(short)]), 2)
        bad = self.root / "bad.jsonl"
        bad.write_text(labels_from([(k, "remembered") for k in keys]), encoding="utf-8",
                       newline="\n")
        self.assertEqual(A.main(["validate", "--worklist", str(self.worklist),
                                 "--labels", str(bad)]), 2)
        partial = self.root / "partial.jsonl"
        partial.write_text(labels_from([(k, "supported") for k in keys[:2]]), encoding="utf-8",
                           newline="\n")
        self.assertEqual(A.main(["validate", "--worklist", str(self.worklist),
                                 "--labels", str(partial)]), 2)

    def test_agreement_refused_before_worklist_closed(self):
        keys = [i["row_key"] for i in A.read_jsonl(self.worklist)[1:]]
        a, b = self.root / "a.jsonl", self.root / "b.jsonl"
        a.write_text(labels_from([(k, "supported") for k in keys]), encoding="utf-8", newline="\n")
        b.write_text(labels_from([(k, "contradicted") for k in keys]), encoding="utf-8", newline="\n")
        self.assertEqual(A.main(["agreement", "--worklist", str(self.worklist),
                                 "--a", str(a), "--b", str(b)]), 2)

    def test_kappa_and_exact_bound_after_closure(self):
        keys = [i["row_key"] for i in A.read_jsonl(self.worklist)[1:]]
        # 4 行：3 同 1 异 → po=0.75；A 全 supported，B 三个 supported 一个 contradicted
        a = self.root / "a.jsonl"
        b = self.root / "b.jsonl"
        a.write_text(labels_from([(k, "supported") for k in keys]), encoding="utf-8", newline="\n")
        b.write_text(labels_from(list(zip(keys, ["supported"] * 3 + ["contradicted"]))),
                     encoding="utf-8", newline="\n")
        closed = self.root / "closed.jsonl"
        rows = A.read_jsonl(self.worklist)
        rows[0]["state"] = "closed"
        closed.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                          encoding="utf-8", newline="\n")
        out = self.root / "ag.json"
        self.assertEqual(A.main(["agreement", "--worklist", str(closed), "--a", str(a),
                                 "--b", str(b), "--out", str(out)]), 0)
        rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertAlmostEqual(rep["observed_agreement"], 0.75)
        self.assertEqual(rep["items"], 4)
        self.assertIsNotNone(rep["agreement_exact95_lower_bound"])
        # 未揭示判官：human_vs_judge 为空
        self.assertIsNone(rep["human_vs_judge"])
        # 判官钥匙私有目录生成后才揭示
        priv = self.root / "private"
        self.assertEqual(A.main(["emit-judge-keys", "--worklist", str(closed),
                                 "--score-dir", str(self.pkg), "--private-dir", str(priv)]), 0)
        self.assertEqual(A.main(["agreement", "--worklist", str(closed), "--a", str(a),
                                 "--b", str(b), "--judge-keys", str(priv / "judge-keys.jsonl"),
                                 "--out", str(self.root / "ag2.json")]), 0)
        rep2 = json.loads((self.root / "ag2.json").read_text(encoding="utf-8"))
        self.assertIn("rater-a", rep2["human_vs_judge"])
        # insufficient 也是三态标签之一（真人同样可以判"证据不足"）⇒ 四行全部参与比对
        self.assertEqual(rep2["human_vs_judge"]["rater-a"]["n"], len(keys))
        judge_labels = [k["judge_result"] for k in A.read_jsonl(priv / "judge-keys.jsonl")]
        self.assertIn("insufficient", judge_labels, "判官的 insufficient 也必须进比对，不静默丢")
        self.assertEqual(len(rep2["disagreements"]), 1)

    def test_judge_keys_refuses_to_overwrite_and_covers_every_row(self):
        priv = self.root / "priv"
        A.main(["emit-judge-keys", "--worklist", str(self.worklist), "--score-dir", str(self.pkg),
                "--private-dir", str(priv)])
        self.assertEqual(A.main(["emit-judge-keys", "--worklist", str(self.worklist),
                                 "--score-dir", str(self.pkg), "--private-dir", str(priv)]), 2)

    def test_public_dir_must_be_new(self):
        self.assertEqual(A.main(["make-worklist", "--score-dir", str(self.pkg),
                                 "--out-dir", str(self.pub)]), 2)

    def test_reveal_spans_multiple_source_packages(self):
        """真人共标包跨 round1+round2 两个评分包：只给一个必须响亮报错，两个都给才揭示全。"""
        pkg2 = self.root / "pkg2"
        pkg2.mkdir(parents=True)
        synth_package(pkg2, decided=("contradicted", "supported", "insufficient", "supported"))
        for name in ("results.json", "semantic-requests.json"):     # 换个任务名与请求指纹
            text = (pkg2 / name).read_text(encoding="utf-8")
            (pkg2 / name).write_text(text.replace("agent-a", "agent-b").replace(REQ_SHA, "cd" * 32),
                                     encoding="utf-8", newline="\n")
        pub2 = self.root / "pub2"
        self.assertEqual(A.main(["make-worklist", "--score-dir", str(pkg2),
                                 "--out-dir", str(pub2)]), 0)
        rows1 = A.read_jsonl(self.worklist)
        rows2 = A.read_jsonl(pub2 / "worklist.jsonl")
        cross = [dict(rows1[0], items=len(rows1) - 1 + len(rows2) - 1)] + rows1[1:] + rows2[1:]
        pubc = self.root / "pubc"
        pubc.mkdir()
        (pubc / "worklist.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in cross),
            encoding="utf-8", newline="\n")
        self.assertEqual(A.main(["emit-judge-keys", "--worklist", str(pubc / "worklist.jsonl"),
                                 "--score-dir", str(self.pkg),
                                 "--private-dir", str(self.root / "priv_part")]), 2)
        self.assertEqual(A.main(["emit-judge-keys", "--worklist", str(pubc / "worklist.jsonl"),
                                 "--score-dir", str(self.pkg), "--score-dir", str(pkg2),
                                 "--private-dir", str(self.root / "priv_full")]), 0)
        self.assertEqual(len(A.read_jsonl(self.root / "priv_full" / "judge-keys.jsonl")),
                         len(cross) - 1)

    def test_conflicting_answers_across_packages_are_refused(self):
        pkg2 = self.root / "pkg_conflict"
        pkg2.mkdir(parents=True)
        synth_package(pkg2, decided=("contradicted", "contradicted", "contradicted",
                                     "contradicted"))          # 同 task/check/指纹，答案不同
        with self.assertRaises(ValueError) as ctx:
            A.load_packages([str(self.pkg), str(pkg2)])
        self.assertIn("conflicting judge answer", str(ctx.exception))

    def test_unanswered_insufficient_is_not_sampled_as_a_judge_answer(self):
        pkg = self.root / "pkg_unanswered"
        pkg.mkdir(parents=True)
        synth_package(pkg)
        rows, _ = A.load_package(pkg)
        for r in rows:
            if r["judge_result"] == "insufficient":
                r["judge_error"] = "FactError"
        answered = [r for r in rows if r["judge_result"] in A.LABELS and not r.get("judge_error")]
        self.assertEqual(len(answered), 3, "四行里有一行是判官没答，不能进一致率分母")

    def test_every_public_item_carries_its_evidence_scope(self):
        """实测过的回归：request 的作用域在 task_context.scope.scope，
        早先按 request.scope/context.scope 取会让 30 道题的 item.scope 全为 null，
        标注者看不出这是记忆题/回复题/产物题。"""
        for item in A.read_jsonl(self.worklist)[1:]:
            self.assertEqual(item["item"]["scope"], "memory_after", item["row_key"])
            self.assertIn("requirement", item["item"])
            self.assertIn("observations", item["item"])
            self.assertTrue(item["item"]["observations"], item["row_key"])
            self.assertNotIn("source_binding", json.dumps(item["item"]),
                             "指纹对人没有判断价值，不该塞进盲题")

    def test_clopper_pearson_matches_independent_solver(self):
        """回归：非全一致那支二分此前方向写反（3/7 报成 0.87，正确是 0.13），
        而错误值曾被写进评审文档与 notes。这里用独立实现交叉核对。"""
        from math import comb

        def ref(k, n, alpha=0.05):
            tail = lambda p: sum(comb(n, i) * p ** i * (1 - p) ** (n - i)
                                 for i in range(k, n + 1))
            lo, hi = 0.0, 1.0
            for _ in range(200):
                mid = (lo + hi) / 2
                if tail(mid) > alpha:
                    hi = mid
                else:
                    lo = mid
            return (lo + hi) / 2
        for k, n in ((30, 45), (10, 15), (3, 7), (1, 3), (6, 12), (14, 14)):
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(A.exact_lower_bound(k, n), ref(k, n), places=4)
        self.assertIsNone(A.exact_lower_bound(0, 0))

    def test_blind_row_carries_no_scoring_side_metadata(self):
        """on_failure/evidence_tier 会把标注者往类别上带，只能待在私有侧。"""
        blob = (self.worklist).read_text(encoding="utf-8")
        for meta in ('"on_failure"', '"evidence_tier"', '"round"'):
            self.assertNotIn(meta, blob, meta)
        first = A.read_jsonl(self.worklist)[1]
        self.assertEqual(set(first), {"row_key", "item", "item_sha256"})

    def test_cohen_matches_hand_computation(self):
        # 两标注者各自 50/50 分布在两类上且完全独立 → pe=0.5
        pairs = [("supported", "supported"), ("supported", "contradicted"),
                 ("contradicted", "supported"), ("contradicted", "contradicted")]
        kappa, po, pe = A.cohen(pairs, ["supported", "contradicted"])
        self.assertAlmostEqual(po, 0.5)
        self.assertAlmostEqual(pe, 0.5)
        self.assertAlmostEqual(kappa, 0.0)

    def test_exact_bound_closed_form_for_perfect_agreement(self):
        self.assertAlmostEqual(A.exact_lower_bound(14, 14), 0.05 ** (1 / 14))
        self.assertIsNone(A.exact_lower_bound(0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
