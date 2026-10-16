#!/usr/bin/env python3
"""交付卷必须能**逐字节重算**：RUN_ON_OPENKYLIN.md 向 A 承诺"重跑逐字节一致"，这条测试兑现它。

这条测试是它自己把我抓出来的：我为了标注判定时点，给每条 memory_entry 检查项的 `basis`
追加了 `, evaluate_at=snapshot_after`，于是 four-layer-r1.json 再也复现不出已交付的
`5bed443d…`——文档承诺当场变成假话，而当时的 60 项审计全绿（审计器只看落盘文件里的数字，
不看"能不能重算"）。修法：默认值不追加，只有显式 `snapshot_before` 才标注。

矩阵里的 `products.*` 记录的是本次输出路径，重跑到别处必然不同 ⇒ 只比对其中内嵌的 sha。
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

CARDS = pathlib.Path(r"D:/a_shanghai/_review/four-layer/s8-batch18-candidate-design"
                     "/cards-r3/candidate-cards.json")
R1_PAPER_SHA = "f30541ae271690a5d07511a5260a22386224bcadd753e075da40cf788ff5cc60"
R1_JSONL_SHA = "0da837550437684b3ac8eec7c415faa5c0b65d0629cf777ad3d1be3d9d1f7867"
R1_PLAN_SHA = "5bed443dabb46be33a84cbff19d490c1c7d6832654bf8d855e99fa1b6329db97"
ARGS = ["tools/freeze_candidates.py", "--extra-suite", "samples/v1.0-batchA.yaml",
        "--allow-restart", "--include-long-wait",
        "--allow-time-mode", "cross_session_only", "--allow-time-mode", "actual_elapsed",
        "--allow-time-mode", "logical_calendar_reference", "--target-samples", "100"]


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class PaperR1ReproducibilityTests(unittest.TestCase):
    @unittest.skipUnless(CARDS.is_file(), "题源卡片不在本机（干净环境跳过，不假红）")
    def test_r1_rebuilds_byte_identical(self):
        tmp = pathlib.Path(tempfile_dir())
        self.addCleanup(lambda: shutil.rmtree(str(tmp), ignore_errors=True))
        out = {k: tmp / k for k in ("paper-r1.yaml", "four-layer-r1.json", "paper-r1.jsonl",
                                    "matrix.json")}
        cmd = [sys.executable] + ARGS + [
            "--out-paper", str(out["paper-r1.yaml"]), "--out-plan", str(out["four-layer-r1.json"]),
            "--out-jsonl", str(out["paper-r1.jsonl"]), "--out-matrix", str(out["matrix.json"])]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, encoding="utf-8",
                              errors="replace", timeout=900, env=env_with_deps())
        self.assertEqual(proc.returncode, 0, proc.stdout[-800:] + proc.stderr[-800:])
        self.assertEqual(sha(out["paper-r1.yaml"]), R1_PAPER_SHA, "题卷不可复现")
        self.assertEqual(sha(out["paper-r1.jsonl"]), R1_JSONL_SHA, "编译产物不可复现")
        self.assertEqual(sha(out["four-layer-r1.json"]), R1_PLAN_SHA,
                         "四层计划不可复现（basis/派生规则改动会先在这里变红）")
        mat = json.loads(out["matrix.json"].read_text(encoding="utf-8"))
        shipped = json.loads((ROOT / "notes/paper-r1-coverage-matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(mat["totals"], shipped["totals"], "矩阵计数漂移")
        prod = mat["products"]
        self.assertEqual(prod["paper_yaml_sha256"], R1_PAPER_SHA)
        self.assertEqual(prod["plan_sha256"] if "plan_sha256" in prod else sha(out["four-layer-r1.json"]),
                         R1_PLAN_SHA)


def tempfile_dir() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="okb-r1-repro-")


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


if __name__ == "__main__":
    unittest.main(exit=False)
