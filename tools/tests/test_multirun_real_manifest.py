"""真实 manifest 必须能进预登记与汇总链（离线，无网络、无智能体调用）。

锁住四件实测踩过的坑：
1. manifest 里必须带 adapter_version / executor_version / observation_scope，且值是真实
   代码哈希（曾经的 bug：module_version 里用了未导入的 pathlib → 一律 '@unknown'，
   登记照样通过，于是"版本一致"变成空承诺）；
2. identity() 允许如实声明的未上报值（model/temperature 由 Agent CLI 上报才有值：
   openclaw 从 meta 取、Hermes-Q 根本不给），但仍然拒绝不一致与垃圾值；
3. 登记门过去硬要求 evidence_policy == 'scoped-v1' ⇒ 默认主链（scoped-v2）根本登记不了；
4. 声明式适配器 agent_kind='cli-template' 过去不在允许枚举里，第三方智能体无法进汇总计划。
"""
import copy
import hashlib
import json
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
from engine import runner                                     # noqa: E402
from engine.adapter import (AgentAdapter, Capabilities, MemorySnapshot,  # noqa: E402
                            TurnResult)
from engine.runner import init_manifest, module_version, observation_scope  # noqa: E402
from scoring.fact_judge import VERSION as FJ_VERSION, SYSTEM  # noqa: E402
from scoring.multirun import identity, validate_registration  # noqa: E402
from scoring.report_v2 import sha                            # noqa: E402


class TinyAdapter(AgentAdapter):
    """最小合成适配器：只为造出真实 manifest，不产生任何证据。"""

    agent_kind = "mock"
    model_name = "synthetic-no-model"
    temperature = 0

    def __init__(self, name="a"):
        super().__init__(dict(adapter_version="synthetic-v1", executor_version="synthetic-runner-v1",
                              observation_scope={"kind": "synthetic-workspace", "memory": "none"}))
        self.name = name

    def capabilities(self):
        return Capabilities(dialogue=True, file=True)

    def reset(self, ctx):
        return []

    def send(self, ctx, session, turn_index, text, session_first_turn, timeout=None):
        return TurnResult("done", "done", "", 0, 1, model=self.model_name)

    def probe_memory(self, phase, *, for_task_id=None):
        return MemorySnapshot(phase, "2026-10-04T00:00:00+00:00", [],
                              {"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0,
                               "sources": []})


def declared(name="a", **over):
    adapter = TinyAdapter(name)
    row = dict(name=adapter.name, agent_kind=adapter.agent_kind, model=adapter.model_name,
               temperature=adapter.temperature, config=adapter.config, config_files=[],
               capabilities=adapter.capabilities().as_dict())
    row.update(over)
    return row


def mini_paper_plan(tmp):
    sample = dict(id="s1", dimension="task_reuse", meta=dict(suite_version="v1.0"),
                  score_policy="rule_only", expect=dict(verdict="remembered"),
                  script=[dict(session="s1", phase="write", turns=[dict(text="写 out.json")])],
                  anchors=[dict(id="A1", type="file_content", path="out.json", expect="present",
                                 severity="hard", on_violation="omitted")])
    check = dict(id="value", layer="objective", op="file_exists", path="out.json",
                 on_failure="omitted", basis="s1.t1")
    paper_sha = None
    paper = tmp / "paper.jsonl"
    paper.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    paper_sha = hashlib.sha256(paper.read_bytes()).hexdigest()
    plan = dict(format="okb-four-layer-v1", paper_sha256=paper_sha,
                samples=[dict(sample_id="s1",
                              anchor_disposition={"A1": dict(role="objective", checks=["value"],
                                                             reason="file existence only")},
                              checks=[check])])
    return paper, plan, paper_sha


class ProvenanceTests(unittest.TestCase):
    def test_module_version_is_a_real_hash_not_unknown(self):
        for mod in ("engine.runner", "engine.adapters.cli_template", "engine.adapters.openclaw",
                    "engine.adapters.hermes"):
            name, _, digest = module_version(mod).partition("@")
            with self.subTest(mod=mod):
                self.assertTrue(re.fullmatch("[a-f0-9]{12}", digest), module_version(mod))
                self.assertNotEqual(digest, "unknown", "静默 '@unknown' 会让版本比对变成空承诺")
        self.assertEqual(module_version("no.such.module.at.all").split("@")[1], "unknown")

    def test_observation_scope_populated_from_adapter_interfaces(self):
        scope = observation_scope(TinyAdapter())
        self.assertTrue(scope)
        for key in ("memory_surface_paths", "workspace_ignore", "file_root_relative"):
            self.assertIn(key, scope, key)

    def test_init_manifest_carries_the_three_identity_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_manifest(pathlib.Path(tmp), "probe-run", "v1.0",
                                     [TinyAdapter("a"), TinyAdapter("b")])
            for agent in manifest["agents"]:
                cfg = agent["config"]
                for key in ("adapter_version", "executor_version", "observation_scope"):
                    self.assertIn(key, cfg, agent["name"] + ":" + key)
                # 声明值优先：适配器自己声明了 adapter_version 就不能被派生值覆盖
                self.assertEqual(cfg["adapter_version"], "synthetic-v1", agent["name"])
                self.assertEqual(cfg["executor_version"], "synthetic-runner-v1",
                                 "声明值优先，不能被派生值覆盖")
                identity(agent)                     # 不抛即说明 manifest 可直接进登记

    def test_undeclared_adapter_version_falls_back_to_real_hash(self):
        class Bare(TinyAdapter):
            def __init__(self, name="bare"):
                super().__init__(name)
                self.config = {}        # 不声明任何版本 → 必须由代码哈希派生

        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_manifest(pathlib.Path(tmp), "probe-run", "v1.0", [Bare()])
            cfg = manifest["agents"][0]["config"]
            self.assertTrue(re.search("@[a-f0-9]{12}$", cfg["adapter_version"]), cfg["adapter_version"])
            self.assertTrue(re.search("@[a-f0-9]{12}$", cfg["executor_version"]), cfg["executor_version"])
            self.assertTrue(cfg["observation_scope"], "scope 派生不能是空的")


class IdentityToleranceTests(unittest.TestCase):
    def test_unknown_model_and_temperature_accepted_as_declared(self):
        out = identity(declared(model=None, temperature=None))
        self.assertIsNone(out["model"])
        self.assertIsNone(out["temperature"])

    def test_declarative_adapter_kind_accepted(self):
        self.assertEqual(identity(declared(agent_kind="cli-template"))["agent_kind"],
                         "cli-template")

    def test_garbage_identity_still_rejected(self):
        for bad in (dict(model=12), dict(temperature="high"), dict(temperature=float("nan")),
                    dict(agent_kind="guessme"), dict(name=""), dict(name=" ")):
            with self.subTest(**{k: repr(v) for k, v in bad.items()}):
                with self.assertRaises(ValueError):
                    identity(declared(**bad))


class RegistrationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.paper, self.plan, self.paper_sha = mini_paper_plan(root)
        self.samples = [json.loads(l) for l in
                        self.paper.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.plan_sha = hashlib.sha256(json.dumps(
            self.plan, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def registration(self, policy):
        agent = identity(declared("a"))
        return dict(format="okb-multirun-plan-v1-draft", comparison_id="t",
                    paper_sha256=self.paper_sha, plan_sha256=self.plan_sha,
                    protocol=dict(outcome_version="okb-four-layer-v2", evidence_policy=policy,
                                  mode="prepare", judge_model=None, fact_protocol=FJ_VERSION,
                                  system_sha256=sha(SYSTEM.encode())),
                    agents={"a": agent},
                    batches=[dict(run_id="r1", tasks=[dict(
                        task_id="a-s1", agent="a", sample_id="s1", repeat=1,
                        attempt_id="att-1", retry_of=None)])],
                    attempt_policy="first_attempt_only")

    def validate(self, policy):
        reg = self.registration(policy)
        # plan_sha256 必须等于计划文件指纹：这里直接按 multirun 的 sha() 口径重算
        from scoring.report_v2 import sha as _sha
        plan_bytes = json.dumps(self.plan, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        reg["plan_sha256"] = _sha(plan_bytes)
        return validate_registration(reg, self.samples, self.plan, self.paper_sha,
                                     reg["plan_sha256"])

    def test_both_scoped_policies_are_registrable(self):
        for policy in ("scoped-v1", "scoped-v2"):
            with self.subTest(policy=policy):
                result = self.validate(policy)
                self.assertIn("r1", result[0])

    def test_legacy_policy_still_refused_for_pooled_runs(self):
        with self.assertRaises(ValueError):
            self.validate("legacy")


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
