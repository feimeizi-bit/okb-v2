#!/usr/bin/env python3
"""okb · 一键主链路：运行 → 采集 → 自动评分 → 报告 → 雷达图。

对应赛题交付 d（openKylin 上一键运行、支持导入不同智能体配置做批量对比）与交付 b
（示例任务/示例运行证据/示例评分结果一次产齐）。设计约束：

- **编排而非重写**：运行走 engine.runner.run_batch，评分走 tools.score_four_layer，
  报告走 tools.report_four_layer，雷达走 tools.scoring.radar_v2；本模块只做参数装配、
  阶段落档与状态汇总，不含第二套业务逻辑（与 okb/cli.py 的 thin dispatcher 同一口径）。
- **默认离线**：评分默认 prepare（0 次 HTTP、0 费用）。真实判官必须显式把 --live 等参数
  经 `--score-arg` 透传给评分器，本模块不自造预算/合同参数。
- **默认新版口径**：`--outcome-version v2 --evidence-policy scoped-v2`（分层证据门）。
  旧口径仍可显式选择，历史产物不被改写；输出目录必须不存在（与仓内既有工具一致）。
- 每个阶段单独落档，失败阶段不影响已完成阶段的产物可读性；`run-summary.json` 是
  「这条链跑到哪一步、产出了什么、证据等级如何」的单一入口。

用法（最小）：
    python -m okb run --agents deploy/agents.yaml --paper build/paper-v0.2.jsonl \\
        --plan samples/four-layer-pilot.v1.json --run-id okb_run_001
只重跑评分与报告：加 `--skip-run --run-id <已有 run-id>`。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

SUMMARY_FORMAT = "okb-run-summary-v1"
AGENTS_FORMAT = "okb-agents-v1"
STAGES = ("run", "score", "report", "radar")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_new(path: Path, obj) -> None:
    """新建写入：显式 UTF-8 + LF（与 A 侧 F1 跨平台字节确定口径一致），拒绝覆盖。"""
    if path.exists():
        raise ValueError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def load_agents(path: Path) -> dict:
    """agents.yaml → {version, repeat, agents:[{name, adapter, config}], notes}."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("agents file must be a mapping")
    if data.get("version", AGENTS_FORMAT) != AGENTS_FORMAT:
        raise ValueError("agents file version must be " + AGENTS_FORMAT)
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("agents file needs a non-empty 'agents' list")
    seen: set[str] = set()
    for row in agents:
        if not isinstance(row, dict):
            raise ValueError("each agent entry must be a mapping")
        name = row.get("name")
        adapter = row.get("adapter", name)
        if not name or not adapter:
            raise ValueError("agent entry needs name and adapter")
        if name in seen:
            raise ValueError(f"duplicate agent name {name!r}: same task_id would mix streams/overwrite raw")
        seen.add(name)
        if row.get("config") is not None and not isinstance(row["config"], dict):
            raise ValueError(f"agent {name!r} config must be a mapping")
    repeat = data.get("repeat", 1)
    if not isinstance(repeat, int) or repeat < 1:
        raise ValueError("repeat must be an integer >= 1")
    return {"version": AGENTS_FORMAT, "repeat": repeat, "agents": agents,
            "note": data.get("note")}


def build_adapters(rows: list[dict]):
    from engine.adapter import AgentAdapter  # noqa: F401  (typing only)
    from engine.runner import make_adapter

    out = []
    for row in rows:
        config = dict(row.get("config") or {})
        config.setdefault("agent_name", row["name"])
        adapter = make_adapter(row["adapter"], config or None)
        # 名称以 YAML 为准：同名多实例在 load_agents 已被拒绝，task_id 前缀必须唯一。
        adapter.name = row["name"]
        out.append(adapter)
    return out


def stage_run(args, agents_cfg, out_root: Path) -> dict:
    from engine.runner import load_paper, resolve_run_dir, run_batch

    suite_version, samples = load_paper(args.paper)
    if args.sample_id:
        wanted = set(args.sample_id.split(","))
        samples = [s for s in samples if s.get("id") in wanted]
        if not samples:
            raise ValueError("no paper sample matched --sample-id")
    adapters = build_adapters(agents_cfg["agents"])
    plan_rows = [{"name": a.name, "adapter": type(a).__name__,
                  "capabilities": (a.capabilities().as_dict()
                                    if callable(getattr(a, "capabilities", None)) else None),
                  "config": dict(getattr(a, "config", {}) or {})} for a in adapters]
    if args.dry_run:
        return {"stage": "run", "status": "dry_run", "samples": len(samples),
                "suite_version": suite_version, "repeat": agents_cfg["repeat"],
                "agents": plan_rows, "run_dir": None,
                "note": "dry run: adapters built and capability snapshot taken, no agent invoked"}

    run_dir, tasks = run_batch(adapters=adapters, samples=samples, suite_version=suite_version,
                               run_id=args.run_id, runs_dir=args.runs_dir,
                               repeat=agents_cfg["repeat"], task_timeout=args.task_timeout,
                               turn_timeout=args.turn_timeout, task_retries=args.task_retries,
                               max_consecutive_invalid=args.max_consecutive_invalid,
                               max_tokens=args.max_tokens, min_free_mb=args.min_free_mb,
                               health_interval=args.health_interval,
                               resume=args.resume, paper_path=args.paper, echo=False,
                               guard_existing=not args.overwrite_run)
    counts = collections.Counter(str(t.get("status")) for t in tasks)
    return {"stage": "run", "status": "done", "run_dir": str(run_dir), "tasks": len(tasks),
            "task_status": dict(counts), "samples": len(samples), "suite_version": suite_version,
            "repeat": agents_cfg["repeat"], "agents": plan_rows,
            "guards": {"max_tokens": args.max_tokens, "min_free_mb": args.min_free_mb,
                       "health_interval": args.health_interval},
            "planned_run_dir_note": str(resolve_run_dir(args.runs_dir, args.run_id, None))}


def stage_score(args, agents_cfg, out_root: Path, run_dir: Path) -> dict:
    from score_four_layer import main as score_main

    score_dir = out_root / "score"
    argv = ["--paper", str(args.paper), "--plan", str(args.plan), "--run-dir", str(run_dir),
            "--out-dir", str(score_dir), "--outcome-version", args.outcome_version,
            "--evidence-policy", args.evidence_policy] + list(args.score_arg or [])
    rc = int(score_main(argv) or 0)
    summary = json.loads((score_dir / "summary.json").read_text(encoding="utf-8"))
    return {"stage": "score", "status": "done" if rc == 0 else "incomplete", "exit_code": rc,
            "out_dir": str(score_dir), "evidence_policy": summary.get("evidence_policy"),
            "outcome_version": summary.get("format"), "tasks": summary.get("tasks"),
            "evidence_valid": summary.get("evidence_valid"), "assessed": summary.get("assessed"),
            "unassessable": summary.get("unassessable"),
            "task_outcomes": summary.get("task_outcomes"),
            "evidence_tiers": summary.get("evidence_tiers"),
            "artifact_representations": summary.get("artifact_representations"),
            "dependency_reasons": summary.get("dependency_reasons"),
            "mode": summary.get("mode"), "limits": summary.get("limits")}


def stage_report(args, out_root: Path, run_dir: Path) -> dict:
    from report_four_layer import main as report_main

    report_dir = out_root / "report"
    argv = ["--score-dir", str(out_root / "score"), "--run-dir", str(run_dir),
            "--paper", str(args.paper), "--plan", str(args.plan), "--out-dir", str(report_dir)]
    if args.verify_traces:
        argv.append("--verify-traces")
    rc = int(report_main(argv) or 0)
    produced = sorted(p.name for p in report_dir.glob("*") if p.is_file())
    return {"stage": "report", "status": "done" if rc == 0 else "incomplete", "exit_code": rc,
            "out_dir": str(report_dir), "files": produced}


def stage_radar(args, out_root: Path) -> dict:
    """Six-dimension radar from the v2 report. Missing stays missing (never 0)."""
    from scoring import radar_v2

    report = json.loads((out_root / "report" / "report.json").read_text(encoding="utf-8"))
    svg = out_root / "radar.svg"
    data = out_root / "radar.json"
    payload = radar_v2.render(report, out_svg=svg, out_json=data,
                              value_key=args.radar_value, error_band=args.radar_error,
                              title=f"okb 六维雷达 · {args.run_id}")
    return {"stage": "radar", "status": "done", "svg": str(svg), "json": str(data),
            "value_key": payload["value_key"], "error_band": payload["error_band"],
            "agents": [a["name"] for a in payload["agents"]],
            "missing_by_agent": {a["name"]: a["missing_dimensions"] for a in payload["agents"]},
            "all_missing": payload["all_missing"], "source_runs": payload.get("source_runs"),
            "limits": payload.get("limits")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", help="智能体配置 YAML（" + AGENTS_FORMAT + "）；与 --agent 二选一")
    ap.add_argument("--agent", action="append", help="直接指定适配器名（可重复，等价于只含该名的 agents.yaml）")
    ap.add_argument("--paper", required=True, help="试卷 JSONL（tools/build_paper.py 产物）")
    ap.add_argument("--plan", required=True, help="四层评分计划 JSON（绑定 paper sha256）")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out-root", default=None, help="本次交付目录，默认 build/run-<run-id>")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--repeat", type=int, default=None, help="覆盖 agents.yaml 的 repeat")
    ap.add_argument("--sample-id", default=None, help="逗号分隔样本 id 过滤")
    ap.add_argument("--outcome-version", choices=["v1", "v2"], default="v2")
    ap.add_argument("--evidence-policy", choices=["legacy", "scoped-v1", "scoped-v2"],
                    default="scoped-v2", help="默认分层证据门；legacy/scoped-v1 保留旧口径")
    ap.add_argument("--score-arg", action="append", default=[],
                    help="原样透传给评分器的参数（可重复；判官 live/replay 等一律由此显式传入）")
    ap.add_argument("--task-timeout", type=float, default=900.0)
    ap.add_argument("--turn-timeout", type=float, default=None)
    ap.add_argument("--task-retries", type=int, default=1)
    ap.add_argument("--max-consecutive-invalid", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="token 预算闸（超限优雅停机，可 --resume；注意 Hermes usage 采不到、不计入）")
    ap.add_argument("--min-free-mb", type=float, default=512.0,
                    help="磁盘守卫阈值 MB（preflight 与任务间检查；低于即停）")
    ap.add_argument("--health-interval", type=float, default=300.0,
                    help="轮间看门狗间隔（秒；embed-server 等健康检查）")
    ap.add_argument("--resume", action="store_true", help="续跑已有 run（需已存在的 run 目录）")
    ap.add_argument("--overwrite-run", action="store_true",
                    help="允许复用同名 run 目录（默认拒绝，防误覆盖既有证据）")
    ap.add_argument("--skip-run", action="store_true", help="只评分/报告/雷达，不运行智能体")
    ap.add_argument("--skip-score", action="store_true", help="只运行与采集证据")
    ap.add_argument("--dry-run", action="store_true",
                    help="只装配适配器并打印计划：不调用智能体、不写任何产物（可安全重复，不占用 run-id）")
    ap.add_argument("--no-radar", action="store_true", help="跳过雷达图阶段")
    ap.add_argument("--radar-value", default="assessed_task_pass_rate",
                    choices=["assessed_task_pass_rate", "complete_sample_mean",
                             "sample_coverage", "common_unit_pass_rate"])
    ap.add_argument("--radar-error", default="range", choices=["range", "sd", "none"])
    ap.add_argument("--verify-traces", action="store_true",
                    help="报告阶段离线重建检查/请求/引文（透传给 report-vnext）")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.outcome_version != "v2" and args.evidence_policy != "legacy":
        print("scoped evidence requires v2 outcomes", file=sys.stderr)
        return 2
    if (args.agents is None) == (args.agent is None) and not (args.skip_run and not args.agents):
        print("need exactly one of --agents <yaml> or --agent <name> (repeatable); "
              "with --skip-run both may be omitted and agents are read from the run manifest",
              file=sys.stderr)
        return 2

    if args.agents:
        agents_cfg = load_agents(Path(args.agents).resolve())
    elif args.skip_run:
        agents_cfg = {"version": AGENTS_FORMAT, "repeat": 1, "agents": [],
                      "note": "score/report/radar only: agent list read from the run manifest"}
    else:
        agents_cfg = {"version": AGENTS_FORMAT, "repeat": 1,
                      "agents": [{"name": n, "adapter": n} for n in args.agent], "note": None}
    if args.repeat is not None:
        agents_cfg["repeat"] = args.repeat

    out_root = Path(args.out_root).resolve() if args.out_root else (ROOT / "build" / f"run-{args.run_id}")
    if not args.dry_run and out_root.exists():
        print(f"output root already exists (refusing to mix deliveries): {out_root}", file=sys.stderr)
        return 2
    run_dir = (Path(args.runs_dir) / args.run_id).resolve()
    if args.skip_run and not (run_dir / "evidence.jsonl").is_file():
        print(f"--skip-run needs an existing run with evidence.jsonl: {run_dir}", file=sys.stderr)
        return 2
    if not args.dry_run:
        out_root.mkdir(parents=True)

    # `--sample-id` 只过滤 run 段的任务集；评分/报告继续用原 plan 会被如实拒绝
    # （score_four_layer 要求 manifest 覆盖 plan 全量样本——fail-closed 不弱化）。
    # 这里自动派生与实跑任务集一致的子集 plan（保留 format/paper_sha256 绑定），
    # 供 score/report 阶段使用；原始 plan 路径记入 run-summary.plan_source。
    plan_source = Path(args.plan).resolve()
    sample_filter = [s for s in (args.sample_id or "").split(",") if s]
    if sample_filter:
        if len(set(sample_filter)) != len(sample_filter):
            print("--sample-id contains duplicate ids", file=sys.stderr)
            return 2
        plan_obj = json.loads(plan_source.read_text(encoding="utf-8"))
        rows = plan_obj.get("samples") if isinstance(plan_obj, dict) else None
        if not isinstance(rows, list) or not rows:
            print(f"plan has no samples list: {plan_source}", file=sys.stderr)
            return 2
        known = {r.get("sample_id") for r in rows}
        unknown = sorted(set(sample_filter) - known)
        if unknown:
            print(f"--sample-id not covered by plan: {','.join(unknown)}", file=sys.stderr)
            return 2
        if set(sample_filter) != known and not args.dry_run:
            subset = dict(plan_obj)
            subset["samples"] = [r for r in rows if r.get("sample_id") in set(sample_filter)]
            subset_path = out_root / "plan.subset.json"
            _write_new(subset_path, subset)
            args.plan = str(subset_path)

    summary = {"format": SUMMARY_FORMAT, "run_id": args.run_id, "started_at": _now(),
               "out_root": str(out_root), "run_dir": str(run_dir) if not args.dry_run else None,
               "paper": str(Path(args.paper).resolve()), "plan": str(Path(args.plan).resolve()),
               "plan_source": str(plan_source) if Path(args.plan).resolve() != plan_source else None,
               "sample_filter": sample_filter,
               "outcome_version": args.outcome_version, "evidence_policy": args.evidence_policy,
               "agents_file": str(Path(args.agents).resolve()) if args.agents else None,
               "agents": [row.get("name") for row in agents_cfg["agents"]],
               "repeat": agents_cfg["repeat"], "score_args": list(args.score_arg or []),
               "stages": {}, "artifacts": []}
    rc_final = 0
    for stage in STAGES:
        try:
            if stage == "run":
                if args.skip_run or args.dry_run:
                    if args.skip_run:
                        info = {"stage": "run", "status": "skipped",
                                "reason": "--skip-run: reuse existing run directory"}
                    else:
                        info = stage_run(args, agents_cfg, out_root)
                else:
                    info = stage_run(args, agents_cfg, out_root)
            elif args.dry_run:
                info = {"stage": stage, "status": "skipped", "reason": "dry run"}
            elif stage == "score":
                if args.skip_score:
                    info = {"stage": stage, "status": "skipped", "reason": "--skip-score"}
                else:
                    info = stage_score(args, agents_cfg, out_root, run_dir)
            elif stage == "report":
                if not (out_root / "score" / "complete.json").is_file():
                    info = {"stage": stage, "status": "skipped",
                            "reason": "no completed score bundle (score stage skipped, failed or incomplete)"}
                else:
                    info = stage_report(args, out_root, run_dir)
            else:
                if args.no_radar or not (out_root / "report" / "report.json").is_file():
                    info = {"stage": stage, "status": "skipped",
                            "reason": "--no-radar" if args.no_radar else "no rendered report (report/report.json)"}
                else:
                    info = stage_radar(args, out_root)
        except Exception as exc:  # noqa: BLE001 —— 阶段隔离：失败要落档，不能把已完成阶段一起丢掉
            info = {"stage": stage, "status": "failed", "error": type(exc).__name__,
                    "detail": str(exc)[:400]}
            rc_final = 2
        summary["stages"][stage] = info
        for key in ("out_dir", "svg", "json", "run_dir"):
            if info.get(key):
                summary["artifacts"].append(str(info[key]))
        if info["status"] == "failed":
            summary["stages_after_aborted"] = [s for s in STAGES if s not in summary["stages"]]
            break

    if not summary["agents"]:
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            summary["agents"] = [a["name"] for a in manifest.get("agents", [])]
            summary["agents_source"] = "run manifest"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            summary["agents_source"] = f"manifest unreadable: {type(exc).__name__}"

    scoring = summary["stages"].get("score", {})
    summary["main_chain"] = {
        "evidence_collected": summary["stages"].get("run", {}).get("status") in ("done", "skipped"),
        "evidence_valid": scoring.get("evidence_valid"),
        "assessed": scoring.get("assessed"),
        "unassessable": scoring.get("unassessable"),
        "evidence_tiers": scoring.get("evidence_tiers"),
        "judge_mode": scoring.get("mode"),
        "verdicts_available": bool(scoring.get("assessed")),
        "note": ("prepare mode never makes semantic passes: assessed=0 is expected until a fact "
                 "judge is explicitly supplied via --score-arg --live/--replay")}
    summary["finished_at"] = _now()
    summary["exit_code"] = rc_final
    if not args.dry_run:
        _write_new(out_root / "run-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return rc_final


if __name__ == "__main__":
    raise SystemExit(main())
