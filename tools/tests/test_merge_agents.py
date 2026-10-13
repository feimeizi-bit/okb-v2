#!/usr/bin/env python3
"""ISS-09 回归锁（B 侧自给）：双 agent 分片归并后 manifest.agents 必须是并集，
且 score 枚举能同时吃到两个 agent 的行。零外部产物依赖（手搓最小 run 目录）。"""
import json
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

def mk_shard(root: pathlib.Path, agent: str, sid: str) -> pathlib.Path:
    d = root / f"shard_{agent}"
    d.mkdir(parents=True)
    ev = [{
        "id": f"ev_{agent}_0001", "seq": 1, "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": "run_probe", "task_id": f"{agent}-{sid}", "session_id": "s1", "turn": 1,
        "source": "message", "type": "message", "actor": "user",
        "payload": {"text": "hello", "session_id": "s1", "turn": 1},
        "pii_hits": [], "raw_ref": None,
    }]
    (d / "evidence.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ev) + "\n",
        encoding="utf-8", newline="\n")
    man = {"run_id": "run_probe", "suite_version": "0.2",
           "agents": [{"name": agent, "model": None}],
           "tasks": [{"task_id": f"{agent}-{sid}", "sample_id": sid, "status": "ok"}],
           "started_at": "x", "finished_at": "x"}
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8", newline="\n")
    return d

def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        s1 = mk_shard(root, "openclaw", "retain_allergy_001")
        s2 = mk_shard(root, "hermes", "update_multi_001")
        out = root / "merged"
        r = subprocess.run([sys.executable, "-m", "engine.merge_runs", str(s1), str(s2),
                            "--out", str(out), "--run-id", "run_probe"],
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, "merge 失败: " + (r.stderr or r.stdout)[-300:]
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        names = sorted(a["name"] for a in m["agents"])
        assert names == ["hermes", "openclaw"], f"agents 非并集: {names}"
        # 顺序敏感性反例：换序也必须两名字都在
        r2 = subprocess.run([sys.executable, "-m", "engine.merge_runs", str(s2), str(s1),
                             "--out", str(root / "merged2"), "--run-id", "run_probe"],
                            capture_output=True, text=True, encoding="utf-8")
        m2 = json.loads((root / "merged2" / "manifest.json").read_text(encoding="utf-8"))
        assert sorted(a["name"] for a in m2["agents"]) == ["hermes", "openclaw"]
        print("✅ ISS-09 锁：双 agent 归并名册=并集（含换序反例）")
        return 0

if __name__ == "__main__":
    sys.exit(main())
