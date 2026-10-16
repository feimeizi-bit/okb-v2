#!/usr/bin/env python3
"""`okb register-run`：跑批**之前**把一次对比封存成不可篡改的预登记文件。

为什么需要它：跨轮诊断（`okb report-multirun`）要吃的 registration 文件此前只能手搓，
于是"这次对比到底比了什么"变成跑完再补的说明文——评审最容易质疑的正是这点。本命令把它
变成一条跑批前就能签发、且可机器复算的凭证，并且**绝不重新实现派生逻辑**：

  · Agent 身份 = 调 `engine.runner.init_manifest()` 得到与真实跑批**同一个** dict，
    再交给 `scoring.multirun.identity()`（adapter_version/executor_version 是模块源码
    sha256，observation_scope 来自适配器接口，config_sha256 是对 config/config_files/
    capabilities 的规范化指纹）；
  · 任务表 = `engine.runner.plan_tasks()`，task_id 与真实跑批逐字一致（含 -rN 规则）；
  · 落盘前先用 `multirun.validate_registration()` 自校验，不通过就不写文件。

诚实边界（用的时候必须知道）：
  1. **登记与跑批必须同机同代码版本**：adapter_version 是源码哈希，改一行就对不上——
     这是有意的 fail-closed，防止"登记 A 代码、跑 B 代码"。所以在 A 的机器上登记、在 A
     的机器上跑。
  2. 独立多轮请用 `--repeat N`（同一 run 内 r1..rN 全是主比较单元）；`--retry-run-id` 只
     给"基础设施失败后重跑留档"用，同单元第二个批次必须 retry_of 链上，主指标只看主尝试。
  3. mode=prepare 时 judge_model 必须为 null（判官还没定，不假装已定）。
  4. 本命令不启动 Agent、不采证据、不评分，也不签名——签名是人的事，它只保证内容一致。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

from engine.runner import init_manifest, load_paper, plan_tasks       # noqa: E402
from run_benchmark import build_adapters, load_agents                # noqa: E402
from scoring.multirun import (FACT_VERSION, IMPORT_VERSION, PLAN_VERSION, SYSTEM,   # noqa: E402
                              identity, sha, validate_registration)

ATTEMPT_POLICY = "first_attempt_only"


def paper_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if not rows:
        raise ValueError("paper has no rows: " + str(path))
    return rows


def agent_identities(agents_cfg: dict, suite_version: str | None) -> tuple[dict, list]:
    """身份必须来自真实跑批的同一条构造路径，否则 config_sha256 一定对不上。

    实测教训：这里原先自己 `make_adapter(row["adapter"], config)`，漏掉 build_adapters 注入的
    `agent_name`，导致登记文件与 runs/…/manifest.json 的 config 指纹不同，report-multirun 直接
    报 `agent configuration mismatch: mock`。派生逻辑只允许有一份，就在那个函数里。
    """
    adapters = build_adapters(agents_cfg["agents"])
    with tempfile.TemporaryDirectory() as td:
        manifest = init_manifest(Path(td), "__registration_probe__", suite_version, adapters)
    out = {}
    for agent in manifest["agents"]:
        out[agent["name"]] = identity(agent)          # 与导入时逐字相同的比较口径
    return out, [a.name for a in adapters]


def build_batches(run_ids: list[str], agent_names: list[str], samples: list[dict],
                  repeat: int) -> list[dict]:
    """primary 批次 + 若干 retry 批次（同单元用 retry_of 串联，满足 first_attempt_only 契约）。"""
    per_run = plan_tasks(agent_names, samples, repeat)
    batches, prev = [], {}
    for i, rid in enumerate(run_ids):
        tasks = []
        for t in per_run:
            unit = (t["agent"], t["sample_id"], t["repeat"])
            tasks.append(dict(task_id=t["task_id"], agent=t["agent"], sample_id=t["sample_id"],
                              repeat=t["repeat"], attempt_id="%s:%s" % (rid, t["task_id"]),
                              retry_of=None if i == 0 else prev.get(unit)))
            prev[unit] = "%s:%s" % (rid, t["task_id"])
        batches.append(dict(run_id=rid, tasks=tasks))
    return batches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", required=True, help="考卷 jsonl（build/paper-r1.jsonl）")
    ap.add_argument("--plan", required=True, help="强制检查计划（samples/four-layer-r1.json）")
    ap.add_argument("--agents", required=True, help="与跑批完全相同的 agents.yaml")
    ap.add_argument("--run-id", action="append", required=True, dest="run_ids",
                    help="批次 run_id，第一个是主批次；再给一个就是它的 retry 留档批次")
    ap.add_argument("--repeat", type=int, default=None, help="全局重复轮数（默认取 agents.yaml）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--comparison-id", default=None)
    ap.add_argument("--mode", choices=("prepare", "replay", "live"), default="prepare")
    ap.add_argument("--judge-model", default=None, help="mode=replay/live 时必填")
    ap.add_argument("--outcome-version", default="okb-four-layer-v2")
    ap.add_argument("--evidence-policy", choices=("scoped-v1", "scoped-v2"), default="scoped-v2")
    ap.add_argument("--emit-imports", default=None,
                    help="顺手产出 report-multirun 的导入清单（目录必须已存在，除非 --allow-missing-imports）")
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--score-root", default=str(ROOT / "build"),
                    help="约定 <score-root>/<run_id>/score（run_benchmark 的默认布局）")
    ap.add_argument("--allow-missing-imports", action="store_true",
                    help="允许导入清单指向尚未跑完的批次（契约把未导入批次计为分母，不许悄悄缩分母）")
    args = ap.parse_args(argv)

    paper_path, plan_path = Path(args.paper).resolve(), Path(args.plan).resolve()
    paper, plan = paper_rows(paper_path), json.loads(plan_path.read_text(encoding="utf-8"))
    agents_cfg = load_agents(Path(args.agents).resolve())
    repeat = args.repeat or int(agents_cfg.get("repeat") or 1)
    if repeat < 1:
        raise ValueError("--repeat must be >= 1")

    selected = {s["sample_id"] for s in plan["samples"]}
    # 样本列表取自跑批自己用的 load_paper()，顺序/内容都不再二次猜测
    suite_version, paper_samples = load_paper(paper_path)
    by_id = {r["id"]: r for r in paper_samples}
    missing = sorted(selected - set(by_id))
    if missing:
        print("计划里有 %d 个样本不在考卷中：%s" % (len(missing), missing[:5]), file=sys.stderr)
        return 2
    # 计划选中集必须是考卷的子集；考卷里有额外题是合法的（用 --sample-id 限定跑批），
    # 但登记会把"必须这样跑"的 --sample-id 一起签发出来，避免登记与跑批样本集不一致。
    subset = set(by_id) == selected
    samples = [by_id[s] for s in sorted(selected)]

    agents, names = agent_identities(agents_cfg, suite_version)
    if len(set(args.run_ids)) != len(args.run_ids):
        print("run_id 重复", file=sys.stderr)
        return 2
    reg = {
        "format": PLAN_VERSION,
        "comparison_id": args.comparison_id or "%s__%s" % (paper_path.stem, plan_path.stem),
        "paper_sha256": sha(paper_path.read_bytes()),
        "plan_sha256": sha(plan_path.read_bytes()),
        "protocol": {
            "outcome_version": args.outcome_version,
            "evidence_policy": args.evidence_policy,
            "mode": args.mode,
            "judge_model": args.judge_model,
            "fact_protocol": FACT_VERSION,
            "system_sha256": sha(SYSTEM.encode()),
        },
        "agents": agents,
        "batches": build_batches(args.run_ids, names, samples, repeat),
        "attempt_policy": ATTEMPT_POLICY,
    }
    if args.mode != "prepare" and not args.judge_model:
        print("mode=%s 必须给出 --judge-model" % args.mode, file=sys.stderr)
        return 2
    if args.mode == "prepare" and args.judge_model:
        print("mode=prepare 不允许声明 --judge-model（判官未定就写 null）", file=sys.stderr)
        return 2

    try:
        batches, attempts, primary = validate_registration(reg, paper, plan,
                                                            reg["paper_sha256"], reg["plan_sha256"])
    except Exception as exc:                                     # noqa: BLE001 —— 自校验失败就不落盘
        print("预登记自校验失败，未写文件：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    digest = sha(out.read_bytes())
    out.with_suffix(out.suffix + ".sha256").write_text("%s  %s\n" % (digest, out.name),
                                                       encoding="utf-8", newline="\n")

    run_hint = "okb run --agents %s --paper %s --plan %s --run-id %s --repeat %d" % (
        args.agents, args.paper, args.plan, args.run_ids[0], repeat)
    if not subset:
        run_hint += " --sample-id " + ",".join(sorted(selected))
    summary = dict(format="okb-registration-v1", registration=str(out), registration_sha256=digest,
                   comparison_id=reg["comparison_id"], paper=str(paper_path), plan=str(plan_path),
                   paper_sha256=reg["paper_sha256"], plan_sha256=reg["plan_sha256"],
                   agents=sorted(agents), repeat=repeat,
                   # validate_registration 返回的 batches 是以 run_id 为键的 dict，
                   # 登记文件里的是 list：摘要统一取登记文件的顺序
                   batches=[b["run_id"] for b in reg["batches"]],
                   planned_tasks=sum(len(b["tasks"]) for b in reg["batches"]), primary_units=len(primary),
                   attempt_policy=ATTEMPT_POLICY, self_validation="passed",
                   paper_rows=len(by_id), plan_selected=len(selected),
                   paper_equals_plan=subset,
                   run_command=run_hint,
                   report_multirun_next="okb report-multirun --registration %s --registration-sha256 %s"
                                        % (out, digest))
    if args.emit_imports:
        entries, run_dir_root = [], Path(args.runs_dir)
        for rid in args.run_ids:
            run_dir, score_dir = run_dir_root / rid, Path(args.score_root) / rid / "score"
            if not args.allow_missing_imports:
                absent = [str(p) for p in (run_dir, score_dir) if not p.is_dir()]
                if absent:
                    print("导入清单缺目录：%s（跑完之后再来，或加 --allow-missing-imports）"
                          % ", ".join(absent), file=sys.stderr)
                    return 2
            entries.append(dict(run_id=rid, score_dir=str(score_dir), run_dir=str(run_dir)))
        imports = dict(format=IMPORT_VERSION, batches=entries)
        ipath = Path(args.emit_imports)
        ipath.parent.mkdir(parents=True, exist_ok=True)
        ipath.write_text(json.dumps(imports, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8", newline="\n")
        summary["imports"] = str(ipath)
        summary["imports_sha256"] = sha(ipath.read_bytes())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
