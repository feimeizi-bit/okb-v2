#!/usr/bin/env python3
"""`okb register-run` 的契约测试：登记必须与真实跑批逐字一致，且不能自己另派生一套身份。

起因（实测）：第一版 register_run 直接 make_adapter(config)，漏了 build_adapters 注入的
`agent_name` ⇒ 登记里的 config_sha256 与 runs/…/manifest.json 不同，report-multirun 报
`agent configuration mismatch: mock`。这类"登记一套、跑一套"的错误只有拿真实 manifest 对撞才抓得到。
"""
import contextlib
import io
import json
import pathlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools" / "tests")]

import register_run as R                                        # noqa: E402
from engine.runner import init_manifest, plan_tasks             # noqa: E402
from run_benchmark import build_adapters, load_agents           # noqa: E402
from scoring.multirun import identity                           # noqa: E402

PAPER_REL = "build/paper-r1.jsonl"
PLAN = str(ROOT / "samples" / "four-layer-r1.json")

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

PAPER_SHA = "0da837550437684b3ac8eec7c415faa5c0b65d0629cf777ad3d1be3d9d1f7867"
PLAN_SHA = "5bed443dabb46be33a84cbff19d490c1c7d6832654bf8d855e99fa1b6329db97"


def run_main(argv):
    """工具会打印 JSON 摘要；测试里吞掉它，否则 unittest 的结果行不再位于输出末尾，
    任何"按末几行判定"的外部脚本（含 deploy/openkylin-verify.sh）都会被噪声骗过。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return R.main(argv)          # 只返回 rc；stdout 已被吞掉，测试一律读落盘文件


class RegisterRunTests(unittest.TestCase):
    def reg_for(self, tmp, extra=()):
        out = Path(tmp) / "registration.json"
        argv = ["--paper", ensure_paper("samples/paper-r1.yaml", PAPER_REL, tmp), "--plan", PLAN, "--agents",
                str(ROOT / "deploy" / "agents.offline.yaml"), "--run-id", "regtest",
                "--repeat", "2", "--mode", "prepare", "--out", str(out)] + list(extra)
        return argv, out

    def test_full_paper_registration_matches_frozen_artifacts(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp)
            self.assertEqual(run_main(argv), 0)
            reg = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(reg["paper_sha256"], PAPER_SHA, "必须绑我们签发的那份考卷")
            self.assertEqual(reg["plan_sha256"], PLAN_SHA)
            self.assertEqual(reg["format"], "okb-multirun-plan-v1-draft")
            self.assertEqual(reg["attempt_policy"], "first_attempt_only")
            tasks = reg["batches"][0]["tasks"]
            self.assertEqual(len(tasks), 200, "100 题 × 1 agent × 2 轮")
            self.assertEqual({t["sample_id"] for t in tasks},
                             {s["sample_id"] for s in reg and json.loads(
                                 (ROOT / "samples" / "four-layer-r1.json")
                                 .read_text(encoding="utf-8"))["samples"]})
            # 副作用文件：sha 旁证必须与文件内容一致
            side = Path(str(out) + ".sha256").read_text(encoding="utf-8").split()[0]
            import hashlib
            self.assertEqual(side, hashlib.sha256(out.read_bytes()).hexdigest())

    def test_registration_identity_equals_real_manifest_identity(self):
        """核心防回归：登记的 agent 必须等于跑批 manifest 经 identity() 后的值。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp)
            self.assertEqual(run_main(argv), 0)
            reg = json.loads(out.read_text(encoding="utf-8"))
            cfg = load_agents(ROOT / "deploy" / "agents.offline.yaml")
            adapters = build_adapters(cfg["agents"])
            with tempfile.TemporaryDirectory() as td:
                man = init_manifest(Path(td), "regtest", None, adapters)
            for agent in man["agents"]:
                self.assertEqual(identity(agent), reg["agents"][agent["name"]],
                                 "登记与跑批的身份派生出现漂移（检查是否绕过了 build_adapters）")

    def test_task_ids_are_exactly_what_plan_tasks_produces(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp)
            self.assertEqual(run_main(argv), 0)
            reg = json.loads(out.read_text(encoding="utf-8"))
            cfg = load_agents(ROOT / "deploy" / "agents.offline.yaml")
            names = [a["name"] for a in cfg["agents"]]
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                paper_path = ensure_paper("samples/paper-r1.yaml", PAPER_REL, td)
                samples = [json.loads(l) for l in Path(paper_path)
                           .read_text(encoding="utf-8").splitlines() if l.strip()]
            want = {t["task_id"] for t in plan_tasks(names, samples, 2)}
            got = {t["task_id"] for t in reg["batches"][0]["tasks"]}
            self.assertEqual(got, want, "task_id 命名必须由 plan_tasks 单点决定（含 -rN 规则）")
            self.assertTrue(any(t.endswith("-r1") for t in got) and any(t.endswith("-r2") for t in got))

    def test_prepare_mode_refuses_declared_judge_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp, ["--judge-model", "gpt-x"])
            self.assertEqual(run_main(argv), 2)
            self.assertFalse(out.exists(), "被拒绝的登记不得留下半截文件")

    def test_replay_mode_requires_judge_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp, ["--mode", "replay"])
            self.assertEqual(run_main(argv), 2)
            self.assertFalse(out.exists())

    def test_unrun_imports_are_refused_unless_allowed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp, ["--emit-imports", str(Path(tmp) / "imports.json"),
                                           "--runs-dir", str(Path(tmp) / "nope")])
            self.assertEqual(run_main(argv), 2, "目录不存在时必须拒绝，不能签发跑不通的导入清单")
            argv2, out2 = self.reg_for(tmp, ["--emit-imports", str(Path(tmp) / "imports2.json"),
                                             "--runs-dir", str(Path(tmp) / "nope"),
                                             "--allow-missing-imports"])
            self.assertEqual(run_main(argv2), 0)
            imp = json.loads((Path(tmp) / "imports2.json").read_text(encoding="utf-8"))
            self.assertEqual(imp["format"], "okb-multirun-imports-v1-draft")

    def test_registration_self_validates_before_writing(self):
        """篡改后的同构登记必须被 validate_registration 拒收（证明自校验不是空话）。"""
        import tempfile
        from scoring.multirun import validate_registration
        with tempfile.TemporaryDirectory() as tmp:
            argv, out = self.reg_for(tmp)
            self.assertEqual(run_main(argv), 0)
            reg = json.loads(out.read_text(encoding="utf-8"))
            paper = [json.loads(l) for l in Path(ensure_paper("samples/paper-r1.yaml", PAPER_REL, tmp))
                     .read_text(encoding="utf-8").splitlines() if l.strip()]
            plan = json.loads((ROOT / "samples" / "four-layer-r1.json").read_text(encoding="utf-8"))
            validate_registration(reg, paper, plan, PAPER_SHA, PLAN_SHA)          # 原样必须通过
            # 挑 validate_registration 真会拦的篡改（不是格式上随便改一个值）
            for mutate, why in (
                (lambda d: d.update(paper_sha256="f" * 64), "考卷指纹与来源不符"),
                (lambda d: d["batches"][0].__setitem__(
                    "tasks", [x for x in d["batches"][0]["tasks"] if not x["task_id"].endswith("-r1")]),
                 "轮次必须从 1 连续（只剩 r2 就是偷改计划）"),
                (lambda d: d["batches"][0]["tasks"].append(dict(d["batches"][0]["tasks"][0])),
                 "任务不得重复"),
            ):
                bad = json.loads(json.dumps(reg))
                mutate(bad)
                with self.assertRaises(Exception, msg=why):
                    validate_registration(bad, paper, plan, PAPER_SHA, PLAN_SHA)

    def test_derivation_is_not_reimplemented(self):
        """源码级守卫：不得绕过 run_benchmark.build_adapters 自己 make_adapter。"""
        src = (ROOT / "tools" / "register_run.py").read_text(encoding="utf-8")
        self.assertIn("build_adapters(", src)
        # 只看真实调用（AST），否则注释里讲教训提到的名字会误报
        import ast
        calls = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                calls.add(name)
        self.assertNotIn("make_adapter", calls,
                         "register_run 直接调用 make_adapter = 又开了第二套身份派生，必然漂移")


if __name__ == "__main__":
    unittest.main(exit=False)
