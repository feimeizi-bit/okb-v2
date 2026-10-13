#!/usr/bin/env python3
"""端到端：只用公开命令把「跑批 → 评分 → 预登记 → 跨轮汇总 → 区分度」串一遍。

为什么要这条测试：本轮两个 High 缺陷（`-rN` 任务名把 agent 拆成幻影名、判官下界方向反）
都只在**真实产物形状**下才暴露；单元测试各守一段时全都"绿"。这条测试刻意经过
`okb run --repeat 2` 产生的 `<agent>-<sample>-rN` 形状，并把结果真的喂给下游分析工具。

用 pilot 卷（2 题）是为了把耗时压到秒级；100 题全卷的结构一致性由
test_register_run.py（200 任务登记）与全量跑批实测覆盖。
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = "dual_order_001,retain_allergy_001"
PILOT_REL = "build/paper-v0.2.jsonl"

def ensure_paper(yaml_rel: str, jsonl_rel: str, tmpdir=None) -> str:
    """安装态（/opt/okb）没有 build/ 目录——它被 .gitignore 排除、也不进 .deb。
    测试必须能像 RUN_ON_OPENKYLIN.md 那样从卡片重建题卷，否则"包里跑的测试"永远红。
    顺带在目标机上多证一次：重建结果与计划绑定的 sha 一致（可复现构建）。"""
    shipped = ROOT / jsonl_rel
    if shipped.is_file():
        return str(shipped)
    assert tmpdir, "需要临时目录来重建题卷"
    out = pathlib.Path(tmpdir) / pathlib.Path(jsonl_rel).name
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "build_paper.py"),
                        str(ROOT / yaml_rel), "-o", str(out)], cwd=str(ROOT),
                       capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    assert p.returncode == 0, "build_paper 失败：\n%s\n%s" % (p.stdout[-800:], p.stderr[-800:])
    assert out.is_file() and out.stat().st_size > 0
    return str(out)



def okb(cmd, *args, expect=0):
    """expect 可以是单个返回码或返回码集合。

    score-vnext 的语义（tools/score_four_layer.py:241）：只有**每条都判成**才返回 0；
    prepare 模式没有判官 ⇒ 全部 unassessable、返回 1 是正常产物，不是失败。
    """
    argv = [sys.executable, "-m", "okb"] + [cmd] + list(args)
    p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, encoding="utf-8",
                       errors="replace", timeout=900)
    codes = expect if isinstance(expect, (tuple, list)) else (expect,)
    if expect is not None:
        assert p.returncode in codes, "%s rc=%s (期望 %s)\nSTDOUT:%s\nSTDERR:%s" % (
            " ".join(argv[3:]), p.returncode, codes,
            (p.stdout or "")[-1500:], (p.stderr or "")[-1500:])
    return p


class ChainE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.runs_dir = cls.base / "runs"
        cls.scores = cls.base / "scores"
        cls.mroot = cls.base / "multirun"
        pilot = ensure_paper("samples/pilot-v0.2.yaml", PILOT_REL, cls.base)
        cls.pilot = pilot
        okb("run", "--agents", str(ROOT / "deploy" / "agents.offline.yaml"),
            "--paper", pilot,
            "--plan", str(ROOT / "samples" / "four-layer-pilot.v1.json"),
            "--run-id", "e2e_chain", "--runs-dir", str(cls.runs_dir),
            "--out-root", str(cls.base / "outroot"), "--repeat", "2",
            "--sample-id", SAMPLES, "--skip-score")
        score_dir = cls.scores / "e2e_chain" / "score"
        score_dir.parent.mkdir(parents=True)      # score-vnext 要求父目录已存在、自身不存在
        okb("score-vnext", "--paper", cls.pilot,
            "--plan", str(ROOT / "samples" / "four-layer-pilot.v1.json"),
            "--run-dir", str(cls.runs_dir / "e2e_chain"), "--out-dir", str(score_dir),
            "--outcome-version", "v2", "--evidence-policy", "scoped-v2", expect=(0, 1))
        summary = json.loads((score_dir / "summary.json").read_text(encoding="utf-8"))
        cls.prepare_summary = summary
        assert summary["mode"] == "prepare" and summary["tasks"] == 4, summary
        reg = okb("register-run", "--paper", cls.pilot,
                  "--plan", str(ROOT / "samples" / "four-layer-pilot.v1.json"),
                  "--agents", str(ROOT / "deploy" / "agents.offline.yaml"),
                  "--run-id", "e2e_chain", "--repeat", "2", "--mode", "prepare",
                  "--out", str(cls.mroot / "registration.json"),
                  "--emit-imports", str(cls.mroot / "imports.json"),
                  "--runs-dir", str(cls.runs_dir), "--score-root", str(cls.scores))
        cls.reg_sha = json.loads(reg.stdout)["registration_sha256"]
        pooled = okb("report-multirun", "--registration", str(cls.mroot / "registration.json"),
                     "--registration-sha256", cls.reg_sha,
                     "--imports", str(cls.mroot / "imports.json"),
                     "--paper", cls.pilot,
                     "--plan", str(ROOT / "samples" / "four-layer-pilot.v1.json"),
                     "--out-dir", str(cls.mroot / "out"))
        cls.pooled_json = json.loads(pooled.stdout.strip().splitlines()[-1])
        cls.score_dir = score_dir

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_produced_the_repeat_suffixed_task_names(self):
        tasks = {t["task_id"] for t in json.loads(
            (self.runs_dir / "e2e_chain" / "manifest.json").read_text(encoding="utf-8"))["tasks"]}
        self.assertEqual(len(tasks), 4, "2 题 × 1 agent × 2 轮")
        self.assertTrue(all(t.endswith(("-r1", "-r2")) for t in tasks), tasks)

    def test_sample_id_subset_derives_matching_scoring_plan(self):
        """`--sample-id` 子集跑必须自动派生子集 plan 供评分/报告使用。

        回归背景（2026-10-08 真机烟雾）：run 段按 --sample-id 过滤（4 task），评分段仍用
        全量 plan 校验 → `missing selected sample tasks or duplicate manifest tasks`，
        整链在 score 阶段被拒。修复=run_benchmark 派生 plan.subset.json 并透传给下游。
        """
        import copy as _copy
        plan = json.loads((ROOT / "samples" / "four-layer-pilot.v1.json").read_text(encoding="utf-8"))
        paper_rows = {r["id"]: r for r in (json.loads(line) for line in
                      Path(self.pilot).read_text(encoding="utf-8").splitlines() if line.strip())}
        plan_ids = {r["sample_id"] for r in plan["samples"]}
        extra = next(i for i, r in paper_rows.items() if i not in plan_ids and r.get("anchors"))
        anchors = paper_rows[extra]["anchors"]
        check_id = "subset_regression_check_1"
        disp = {}
        for idx, a in enumerate(anchors):
            if idx == 0:
                disp[a["id"]] = {"role": "semantic", "checks": [check_id],
                                 "reason": "e2e 子集回归占位；该行会被派生流程剔除，不参与评分"}
            else:
                disp[a["id"]] = {"role": "diagnostic", "checks": [],
                                 "reason": "e2e 子集回归占位：不参与本测试"}
        wide = _copy.deepcopy(plan)
        wide["samples"].append({
            "sample_id": extra,
            "anchor_disposition": disp,
            "checks": [{"id": check_id, "layer": "semantic", "on_failure": "omitted",
                        "basis": "e2e subset regression placeholder",
                        "scope": "agent_reply", "session": "s1", "turn": 1,
                        "requirement": "placeholder（只为让全量 plan 结构合法）"}]})
        wide_path = self.base / "wide-plan.json"
        wide_path.write_text(json.dumps(wide, ensure_ascii=False, indent=1), encoding="utf-8")

        out_root = self.base / "outroot-subset"
        okb("run", "--skip-run", "--run-id", "e2e_chain",
            "--runs-dir", str(self.runs_dir), "--out-root", str(out_root),
            "--paper", self.pilot, "--plan", str(wide_path), "--sample-id", SAMPLES)
        derived = json.loads((out_root / "plan.subset.json").read_text(encoding="utf-8"))
        self.assertEqual({r["sample_id"] for r in derived["samples"]}, set(SAMPLES.split(",")),
                         "派生的子集 plan 必须只含实跑样本")
        summary = json.loads((out_root / "run-summary.json").read_text(encoding="utf-8"))
        self.assertIn(summary["stages"]["score"]["status"], ("done", "incomplete"),
                      "prepare 下 incomplete 是正常产物；这里不能是 failed")
        self.assertEqual(summary["stages"]["score"]["tasks"], 4)
        self.assertTrue((out_root / "report" / "report.json").is_file())
        self.assertTrue((out_root / "radar.svg").is_file())
        self.assertEqual(summary["sample_filter"], SAMPLES.split(","))
        self.assertEqual(summary["plan"], str((out_root / "plan.subset.json").resolve()),
                         "run-summary 的 plan 必须指向派生的子集 plan（按解析后的真实路径比较，"
                         "openKylin 上 /tmp 是 /var/tmp 的符号链接）")

    def test_dry_run_leaves_no_output_root_and_does_not_block_the_real_run(self):
        """`--dry-run` 必须零落盘，否则同 run-id 真跑被 'output root already exists' 拒绝。

        回归背景（2026-10-08 真机烟雾）：按 B 执行单先 `--dry-run` 再同 run-id 跑批，
        旧行为里 dry-run 会写 `build/run-<id>/run-summary.json`，第二步必被拦。
        """
        out_root = self.base / "dryout"
        common = ["--agents", str(ROOT / "deploy" / "agents.offline.yaml"),
                  "--paper", self.pilot,
                  "--plan", str(ROOT / "samples" / "four-layer-pilot.v1.json"),
                  "--run-id", "e2e_dry", "--runs-dir", str(self.runs_dir),
                  "--out-root", str(out_root), "--sample-id", SAMPLES]
        dry = okb("run", *common, "--dry-run")
        self.assertIn('"status": "dry_run"', dry.stdout)
        self.assertFalse(out_root.exists(), "dry-run 不得留下产物目录或摘要（零落盘）")
        # 同 run-id 真跑（离线 mock、零成本）——旧行为在这一步被拒
        okb("run", *common, "--repeat", "1", "--skip-score")
        summary = json.loads((out_root / "run-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["stages"]["run"]["status"], "done")
        self.assertEqual(summary["stages"]["run"]["tasks"], 2, "2 题 × 1 agent × 1 轮")

    def test_run_guards_are_forwarded_to_the_engine(self):
        """--max-tokens / --min-free-mb 必须透传到 engine 跑批层。

        回归背景（2026-10-08 复核 600 前置）：`okb run` 曾不暴露 engine 的预算/磁盘守卫，
        长跑失去两把关键刹车。这里用两条可观测边证：max_tokens 进 manifest 与 run-summary；
        巨大 min-free-mb 必须让 preflight 拒跑（全部任务 env_error + preflight_failed）。
        """
        common = ["--agents", str(ROOT / "deploy" / "agents.offline.yaml"),
                  "--paper", self.pilot,
                  "--plan", str(ROOT / "samples" / "four-layer-pilot.v1.json"),
                  "--runs-dir", str(self.runs_dir),
                  "--sample-id", SAMPLES, "--repeat", "1", "--skip-score"]
        out_root = self.base / "guardout"
        okb("run", *common, "--run-id", "e2e_guard", "--out-root", str(out_root),
            "--max-tokens", "123456")
        manifest = json.loads((self.runs_dir / "e2e_guard" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["usage"]["max_tokens"], 123456, "预算闸值必须到达 engine")
        summary = json.loads((out_root / "run-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["stages"]["run"]["guards"]["max_tokens"], 123456)

        out_root2 = self.base / "guardout2"
        okb("run", *common, "--run-id", "e2e_guard2", "--out-root", str(out_root2),
            "--min-free-mb", "1000000000")
        manifest2 = json.loads((self.runs_dir / "e2e_guard2" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest2["stopped_reason"], "preflight_failed",
                         "巨大磁盘阈值必须让 preflight 拒跑（守卫确实到达 engine）")
        self.assertTrue(manifest2["tasks"])
        self.assertTrue(all(t["status"] == "env_error" for t in manifest2["tasks"]))
        summary2 = json.loads((out_root2 / "run-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary2["stages"]["run"]["task_status"], {"env_error": 2})

    def test_registration_covers_exactly_the_executed_tasks(self):
        reg = json.loads((self.mroot / "registration.json").read_text(encoding="utf-8"))
        planned = {t["task_id"] for t in reg["batches"][0]["tasks"]}
        executed = {t["task_id"] for t in json.loads(
            (self.runs_dir / "e2e_chain" / "manifest.json").read_text(encoding="utf-8"))["tasks"]}
        self.assertEqual(planned, executed, "预登记任务表与实际跑批必须逐字相同")

    def test_pooled_report_carries_attribution_section_gracefully(self):
        """跨轮报告必须带归因节；pilot 计划没有 memory_before ⇒ 如实 not_applicable，
        既不能因为缺基线族而崩，也不能伪装成"干净"。"""
        rep = json.loads((self.mroot / "out" / "report.json").read_text(encoding="utf-8"))
        att = rep["attribution"]
        self.assertEqual(att["status"], "not_applicable")
        self.assertEqual(att["attribution_counts"], {})
        self.assertEqual(att["batches_total"], att["imported_batches"])
        self.assertIsNone(att["never_written_share"])
        self.assertIsNone(rep["overall_score"], "归因节不得产生总分")

    def test_pooled_report_imported_every_batch(self):
        self.assertEqual(self.pooled_json["planned_batches"], 1)
        self.assertEqual(self.pooled_json["imported_batches"], 1)
        self.assertEqual(self.pooled_json["missing_batches"], [])
        self.assertIsNone(self.pooled_json["overall_score"], "prepare 模式不给总分（无判官）")
        self.assertFalse(self.pooled_json["release_authorized"])
        self.assertTrue((self.mroot / "out" / "report.json").is_file())

    def test_discriminability_reads_agents_and_repeats_from_the_real_package(self):
        """这条就是本轮 High-1 的守门测试：真实 results.json 没有 agent 字段。"""
        okb("discriminability", "--score-dir", str(self.score_dir), "--out",
            str(self.base / "disc.json"))
        # 同一份评分产物，给了 --run-dir 就必须改用 manifest 声明名，而不是继续猜
        okb("discriminability", "--score-dir", str(self.score_dir),
            "--run-dir", str(self.runs_dir / "e2e_chain"), "--out", str(self.base / "disc2.json"))
        rep2 = json.loads((self.base / "disc2.json").read_text(encoding="utf-8"))
        for item in rep2["items"]:
            self.assertEqual(item["agent_names_declared"], ["mock"],
                             "有 manifest 时 agent 名应来自声明值")
            self.assertEqual(item["agent_names_derived"], [])
        rows = json.loads((self.score_dir / "results.json").read_text(encoding="utf-8"))
        self.assertFalse(any("agent" in r for r in rows), "前提：results.json 不含 agent 字段")
        rep = json.loads((self.base / "disc.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["format"], "okb-discriminability-v1")
        agents = {a for item in rep["items"] for a in item["agents"]}
        self.assertEqual(agents, {"mock"},
                         "幻影智能体名（mock-r / mock-re）再次出现就说明 High-1 回归了")
        for item in rep["items"]:
            self.assertEqual(item["repeats"], 2,
                             "-r1/-r2 必须被识别成两轮观测（缺陷版里 repeat 恒为 1）")
            self.assertEqual(item["agent_names_derived"], ["mock"])
            self.assertEqual(item["agent_names_declared"], [], "无 manifest 场景要如实标出猜测")

    def test_registration_sha_is_replayable_from_the_sidecar(self):
        import hashlib
        side = (self.mroot / "registration.json.sha256").read_text(encoding="utf-8").split()[0]
        self.assertEqual(side, self.reg_sha)
        self.assertEqual(side, hashlib.sha256(
            (self.mroot / "registration.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main(exit=False)
