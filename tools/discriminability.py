#!/usr/bin/env python3
"""样本级区分度与稳定性（服务「数据设计质量 25%」里的"样本是否有区分度"这条细则）。

一句话定义：一**题**有区分度 = 不同智能体在它上面被分出高下；一**题**没区分度 = 所有人
都过、所有人都不过，或者谁都判不出来。多轮重复再给一列稳定性（同题同 Agent 跨轮是否一致）。

只吃已完成的 v2 评分包（results.json），因此它统计的是**判定结果**，不是分数猜测：
  - pass/fail 来自 task_outcome；unassessable 单独计数，绝不折算成 0 分
  - 区分度指标 = 跨 Agent 通过率极差（max-min），并给经典 2 组差值 (最高-最低)
  - 全部 unassessable 的题记 indistinguishable_reason=no_assessment，不算"难题"
  - 全过/全败记 saturated，提示这道题对当前 Agent 集合没有信息量
不做什么：不因为"某款 Agent 分低"就改题（那是反向拟合）；不在缺轮次时假装稳定。
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

FORMAT = "okb-discriminability-v1"
GROUPS = ("pass", "fail", "unassessable")


def paper_dimensions(score_dir: Path) -> dict:
    """results.json 不带 dimension；从评分包 input-lock 锁定的试卷里取（不猜、不外查）。"""
    lock = score_dir / "input-lock.json"
    if not lock.is_file():
        return {}
    dims = {}
    for raw in json.loads(lock.read_text(encoding="utf-8")).get("sha256", {}):
        path = Path(raw)
        if path.suffix != ".jsonl" or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            sample = json.loads(line)
            if sample.get("id") and sample.get("dimension"):
                dims[sample["id"]] = sample["dimension"]
    return dims


def known_agents(run_dir: Path) -> set:
    """agent 名以 manifest 为准：任务名前缀匹配不能靠猜。"""
    manifest = run_dir / "manifest.json"
    if not manifest.is_file():
        return set()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return {a["name"] for a in data.get("agents", []) if isinstance(a, dict) and a.get("name")}


derived_from_task_id: set = set()          # 哪些 agent 名是从 task_id 猜出来的（报告要如实标）


def load(score_dir: Path, run_dir: Path | None = None) -> list[dict]:
    """agent 名优先取 manifest（`--run-dir`）；就地找不到才退回评分目录自身。"""
    rows = json.loads((score_dir / "results.json").read_text(encoding="utf-8"))
    derived_from_task_id.clear()
    candidates = [run_dir] if run_dir else []
    candidates += [score_dir, score_dir.parent]
    known = set()
    for c in candidates:
        if c and (c / "manifest.json").is_file():
            known = known_agents(c)
            if known:
                break
    dims = paper_dimensions(score_dir)
    out = []
    for r in rows:
        if not r.get("sample_id"):
            raise ValueError("results row without sample_id: " + str(r.get("task_id")))
        outcome = r.get("task_outcome") or ("assessed" if r.get("status") == "assessed" else "unassessable")
        # task_id 形态是 <agent>-<sample>[-r<N>]（engine/runner 命名规则）。
        # 此前只按 len(sample) 反切，带 -rN 时把 sample 后缀当 agent 尾巴切进来，
        # 得到 "openclaw-re" 这种幻影智能体名，并且 repeat 恒为 1 ⇒ 跨轮稳定性永远算不出。
        # 现在显式剥 -rN，并且只按 manifest 里的真实 agent 名前缀匹配（无 manifest 时回退）。
        tid = r["task_id"]
        sid = r["sample_id"]
        repeat = r.get("repeat") or 1
        m = re.search(r"-r(\d+)$", tid)
        if m:
            repeat = max(repeat, int(m.group(1)))
            stem = tid[: m.start()]
        else:
            stem = tid
        agent = r.get("agent")
        if not agent:
            candidates = [a for a in known if stem.startswith(a + "-")]
            if candidates:
                agent = max(candidates, key=len)      # 最长前缀：agent 名本身可以含 '-'
            elif known:
                raise ValueError("task_id %r matches no agent in manifest.json; refusing to guess "
                                 "an agent name from the string" % tid)
            else:
                agent = stem.split("-", 1)[0]         # 无 manifest 可比对时才回退，并显式标注
                derived_from_task_id.add(agent)
        out.append(dict(sample_id=r["sample_id"], agent=agent, repeat=int(repeat),
                        outcome=outcome, error_class=r.get("error_class"),
                        dimension=r.get("dimension") or dims.get(r["sample_id"]),
                        tier=(r.get("layer1") or {}).get("policy")))
    if not out:
        raise ValueError("empty results package")
    return out


def analyse(rows: list[dict]) -> dict:
    by_sample: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_sample[r["sample_id"]].append(r)
    items = []
    for sid, group in sorted(by_sample.items()):
        per_agent: dict[str, dict[str, int]] = collections.defaultdict(
            lambda: collections.Counter())
        for r in group:
            per_agent[r["agent"]][r["outcome"]] += 1
        rates = {}
        for agent, counter in per_agent.items():
            assessed = counter["pass"] + counter["fail"]
            rates[agent] = (counter["pass"] / assessed) if assessed else None
        known = [v for v in rates.values() if v is not None]
        if not known:
            reason = "no_assessment"
            spread = None
        elif len(known) < 2:
            reason = "single_agent_only"          # 没有对比对象就谈不上区分度
            spread = None
        else:
            spread = max(known) - min(known)
            reason = None if spread >= 0.5 else ("saturated" if spread == 0 else "low")
        # 同 Agent 跨轮一致性（>=2 轮才算）
        stability = {}
        for agent in per_agent:
            per_repeat = {}
            for r in group:
                if r["agent"] == agent:
                    per_repeat.setdefault(r["repeat"], set()).add(r["outcome"])
            if len(per_repeat) >= 2:
                flat = [o for s in per_repeat.values() for o in s]
                stability[agent] = len(set(flat)) == 1
        items.append(dict(sample_id=sid, dimension=group[0].get("dimension"),
                          agents=sorted(rates), agent_pass_rate={a: rates[a] for a in sorted(rates)},
                          outcome_counts={a: dict(per_agent[a]) for a in sorted(per_agent)},
                          discrimination=spread, indistinguishable_reason=reason,
                          agent_names_declared=sorted({r["agent"] for r in group} - derived_from_task_id),
                          agent_names_derived=sorted({r["agent"] for r in group} & derived_from_task_id),
                          assessed_tasks=sum(1 for r in group if r["outcome"] in ("pass", "fail")),
                          unassessable_tasks=sum(1 for r in group if r["outcome"] == "unassessable"),
                          repeats=len({r["repeat"] for r in group}),
                          within_agent_stable=stability or None))
    graded = [i for i in items if i["discrimination"] is not None]
    by_dim = collections.defaultdict(lambda: collections.Counter())
    for i in items:
        d = i.get("dimension") or "unknown"
        by_dim[d]["samples"] += 1
        if i["discrimination"] is not None:
            by_dim[d]["comparable"] += 1
            if i["discrimination"] >= 0.5:
                by_dim[d]["discriminative"] += 1
            if i["indistinguishable_reason"] == "saturated":
                by_dim[d]["saturated"] += 1
    stable = [v for i in items if i["within_agent_stable"] for v in i["within_agent_stable"].values()]
    return dict(format=FORMAT, samples=len(items),
                samples_with_spread=len(graded),
                discriminative_ge_0p5=sum(1 for i in graded if i["discrimination"] >= 0.5),
                mean_spread=(round(statistics.fmean([i["discrimination"] for i in graded]), 4)
                             if graded else None),
                saturated=sum(1 for i in items if i["indistinguishable_reason"] == "saturated"),
                no_assessment=sum(1 for i in items if i["indistinguishable_reason"] == "no_assessment"),
                single_agent_only=sum(1 for i in items
                                      if i["indistinguishable_reason"] == "single_agent_only"),
                repeat_stability=dict(observations=len(stable), consistent=sum(1 for v in stable if v))
                if stable else None,
                by_dimension={d: dict(c) for d, c in sorted(by_dim.items())},
                items=items,
                limits=["discrimination is measured on assessed tasks only; unassessable never "
                        "counts as a failure",
                        "a sample judged for one agent only has no comparison and is reported as "
                        "single_agent_only, not as an easy/hard item",
                        "spread >= 0.5 is the reporting threshold used here, not a standard "
                        "cut-off; the raw per-agent rates are shipped so anyone can re-cut it"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--score-dir", required=True)
    ap.add_argument("--out", default=None, help="写 JSON 报告（默认只打印）")
    ap.add_argument("--csv", default=None, help="额外写题级明细 CSV")
    ap.add_argument("--run-dir", default=None,
                    help="跑批目录（含 manifest.json）。给了就以 manifest 的 agent 名为准，"
                         "不再从 task_id 猜名（评分目录与 runs 目录不同层级时必须给）")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        report = analyse(load(Path(args.score_dir).resolve(),
                              Path(args.run_dir).resolve() if args.run_dir else None))
    except (ValueError, OSError, KeyError) as exc:
        print("rejected: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        out = Path(args.out)
        if out.exists():
            print("refusing to overwrite " + str(out), file=sys.stderr)
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
    if args.csv:
        csv_path = Path(args.csv)
        if csv_path.exists():
            print("refusing to overwrite " + str(csv_path), file=sys.stderr)
            return 2
        cols = ["sample_id", "dimension", "discrimination", "indistinguishable_reason",
                "assessed_tasks", "unassessable_tasks", "repeats"]
        lines = [",".join(cols + ["agent_pass_rate"])]
        for i in report["items"]:
            rates = ";".join("%s=%s" % (a, v) for a, v in i["agent_pass_rate"].items())
            lines.append(",".join(str(i[c]) if i[c] is not None else "" for c in cols)
                         + "," + json.dumps(rates, ensure_ascii=False))
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in
                      ("samples", "samples_with_spread", "discriminative_ge_0p5", "mean_spread",
                       "saturated", "no_assessment", "single_agent_only", "repeat_stability",
                       "by_dimension")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
