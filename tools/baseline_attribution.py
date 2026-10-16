#!/usr/bin/env python3
"""写入归因四象限：把 `omitted` 拆成"从没写进去"与"写进去又丢了"（只诊断，不评分）。

为什么需要：四层评分的五分类里 `omitted` 同时容纳两种**成因相反**的失败——
写入/准入能力失效（记忆面里从来没有过）与保留能力失效（写过又没了）。
paper-r1 没有任务前基线证据（memory_before=0）时无从区分；r2 补上基线后，
可以只用**不依赖正文匹配**的可观测量把这两种分开。

只用这三件事（都在证据里直接可数）：
  · 任务前快照的条目数 `before.entries_count`（>0 = 基线非空，即记忆面在本题开始前已有内容）
  · 本任务期间发生的写入事件数（`memory_write`，含 key/value，来源为 memory_api 或文件观测）
  · 任务后快照的条目数 `after.entries_count`

象限（每个任务一行）：
判定只看**记忆面的终态观测**（第二列 = 任务后快照是否非空）；写入事件只用于发现仪器矛盾。

| 基线空 | 任务后记忆面非空 | 读法 |
|---|---|---|
| 是 | 是 | `write_attributed` 写入可归因于本任务 |
| 是 | 否 | `never_written` ← 以前会被统一记成 omitted 的那一类 |
| 否 | 是 | `residue_plus_write` 基线有残留，本题读数不可归因（污染） |
| 否 | 否 | `residue_cleared` 任务前有、任务后无：基线被清空，异常需人查 |

第五态 `capture_gap`：有写入事件但任务后记忆面读到 0 条目 ⇒ 两个观测自相矛盾，
**不归入任何能力象限、也不当作智能体失败**，只进人审队列（先怀疑观测面）。

**明确不做什么**（这是它的可信度来源，不是谦虚）：
  · 不做正文/关键词匹配 ⇒ 不构成"字符串出现=正确"的判据，不参与评分、不排名、不改五分类。
    条目正文只有在 T2/T3 全文采集回执存在时才有意义，本工具不假设它有。
  · 不下结论说"残留来自哪个任务"：跨任务归属需要任务顺序与 Agent 侧记忆键，这里只报事实。
  · 没有基线题族的分数包会被**拒绝**（防止这个工具悄悄变成通用关键词评分器）。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

FORMAT = "okb-baseline-attribution-v1"
# 五个状态：四象限 + capture_gap。第五态不是失败类，而是"仪器自相矛盾、无法归因"——
# 看到写入事件却读到空记忆面时，绝不能把它圆成"写入成功"（我第一版就是这么错的）。
QUADRANTS = ("write_attributed", "never_written", "residue_plus_write", "residue_cleared",
             "capture_gap")


def read_jsonl(path: pathlib.Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def baseline_samples(plan: dict) -> dict:
    """只有计划里带 memory_before 检查项的题目才属于基线族。"""
    hit = {}
    for row in plan.get("samples", []):
        mb = [c["id"] for c in row.get("checks", []) if c.get("scope") == "memory_before"]
        if mb:
            hit[row["sample_id"]] = mb
    return hit


def per_task(rows: list[dict]) -> dict:
    """task_id -> {before_n, after_n, writes}。快照条目数取探针事件，写入取 memory_write 事件。"""
    acc = collections.defaultdict(lambda: {"before_n": None, "after_n": None, "writes": 0,
                                           "write_keys": []})
    for r in rows:
        tid = r.get("task_id")
        if not tid:
            continue
        p = r.get("payload") or {}
        if r.get("type") == "memory_read" and p.get("phase") in ("before", "after"):
            n = p.get("entries_count")
            if not isinstance(n, int):
                cov = p.get("coverage") or {}
                n = cov.get("entries_scanned")
            if isinstance(n, int):
                key = "before_n" if p["phase"] == "before" else "after_n"
                if acc[tid][key] is None:
                    acc[tid][key] = n
        elif r.get("type") == "memory_write":
            acc[tid]["writes"] += 1
            k = p.get("key")
            if isinstance(k, str) and k and k not in acc[tid]["write_keys"]:
                acc[tid]["write_keys"].append(k)
    return dict(acc)


def flags(before_n: int, writes: int, after_n: int, attribution: str) -> list:
    """事实性旗标（无阈值，触发条件就是可复核的观察本身）。

    旗标只说明"这件事需要人看一眼"，并**不指认是谁的错**：同一个旗标既可能是被测智能体的
    能力问题，也可能是我们评测环境的观测面/重置问题——这正是归因反身检测的目的。
    """
    out = []
    if writes > 0 and after_n == 0:
        out.append("capture_gap_suspect")     # 写入事件可见，记忆面快照却空 ⇒ 观测面可能没覆盖真实存储
    if attribution in ("residue_plus_write", "residue_cleared"):
        out.append("unattributable_residue")  # 基线非空 ⇒ 本题读数不能归因于本次任务
    return out


def quadrant(before_n: int, writes: int, after_n: int) -> str:
    if writes > 0 and after_n == 0:
        return "capture_gap"        # 观测互相矛盾：不替仪器圆场，直接标"无法归因"
    residue = before_n > 0
    retained = after_n > 0
    if not residue and retained:
        return "write_attributed"
    if not residue and not retained:
        return "never_written"
    if residue and retained:
        return "residue_plus_write"
    return "residue_cleared"


def analyse(run_dir: pathlib.Path, plan: dict) -> dict:
    mb = baseline_samples(plan)
    if not mb:
        raise SystemExit("这份计划里没有任何 memory_before 检查项：本工具只服务基线题族，"
                         "拒绝退化成通用关键词评分器。请用带基线族的计划（如 r2）。")
    rows = read_jsonl(run_dir / "evidence.jsonl")
    tasks = per_task(rows)
    out_rows, missing = [], []
    for tid, v in sorted(tasks.items()):
        sid = None
        for sample_id in mb:
            if tid.endswith("-" + sample_id) or ("-" + sample_id + "-r") in tid:
                sid = sample_id
                break
        if sid is None:
            continue                                  # 非基线族任务：不参与归因统计
        if v["before_n"] is None or v["after_n"] is None:
            missing.append(tid)                        # 没有双侧快照就不能谈归因
            continue
        q = quadrant(v["before_n"], v["writes"], v["after_n"])
        out_rows.append(dict(task_id=tid, sample_id=sid, before_entries=v["before_n"],
                             after_entries=v["after_n"], writes=v["writes"],
                             write_keys=sorted(v["write_keys"])[:6], attribution=q,
                             flags=flags(v["before_n"], v["writes"], v["after_n"], q)))
    tally = collections.Counter(r["attribution"] for r in out_rows)
    flag_tally = collections.Counter(f for r in out_rows for f in r["flags"])
    queue = [dict(kind=f, task_id=r["task_id"], sample_id=r["sample_id"],
                  observation="writes=%d, before=%d, after=%d, attribution=%s"
                              % (r["writes"], r["before_entries"], r["after_entries"],
                                 r["attribution"]),
                  # 队列条目的定性只到"需要人看"，不替人下结论
                  suggested_check={"capture_gap_suspect":
                                   "核对该 Agent 的 memory_surface_paths / 观测范围是否覆盖真实记忆存储；"
                                   "写入事件与快照不一致时，先怀疑仪器再怀疑考生",
                                   "unattributable_residue":
                                   "核对该 Agent 的 reset() 是否真的清理记忆面；残留存在时本题的"
                                   "保留类读数不可归因，应排除或重跑"}[f],
                  counts_toward_score=False)
                  for r in out_rows for f in r["flags"]]
    for tid in missing[:50]:
        queue.append(dict(kind="missing_snapshot_pair", task_id=tid, sample_id=None,
                          observation="没有成对 before/after 快照",
                          suggested_check="采集是否被跳过/超时；缺快照的任务不得当成 0 条目",
                          counts_toward_score=False))
    n = len(out_rows)
    return dict(format=FORMAT, baseline_samples=len(mb), tasks_with_both_snapshots=n,
                tasks_without_pair=len(missing), tasks_without_pair_ids=missing[:10],
                attribution_counts=dict(tally),
                attribution_rates={q: (round(tally[q] / n, 4) if n else None) for q in QUADRANTS},
                never_written_share=(round(tally["never_written"] / n, 4) if n else None),
                unattributable_share=(round((tally["residue_plus_write"] + tally["residue_cleared"]
                                             + tally["capture_gap"]) / n, 4) if n else None),
                flag_counts=dict(flag_tally),
                review_queue=queue,
                review_queue_size=len(queue),
                rows=out_rows,
                limits=["诊断量，不参与评分、不排名、不改动五分类读数",
                        "不做正文/关键词匹配：'字符串出现=正确'是卡片包明令禁止的判据",
                        "before.entries_count>0 只说明基线非空（可能来自别的任务或历史会话），"
                        "不推断具体来源",
                        "没有成对 before/after 快照的任务被单列计数，不当作 0 条目",
                        "旗标是事实观察（writes>0 且 after=0 / 基线非空 / 缺成对快照），无自定义阈值；"
                        "它不指认责任方——同一个旗标既可能是智能体能力，也可能是评测环境的观测面或重置问题",
                        "review_queue 只用于派生人审事项，counts_toward_score 恒为 false",
                        "第五态 capture_gap 表示仪器自相矛盾（有写入事件但记忆面为空）：不进任何能力象限，"
                        "也不当作智能体失败"])


def aggregate_reports(per_batch: list[dict]) -> dict:
    """把各批次归因聚合成"跨轮仪器自证"读数。

    关键用法：看 `residue_*` 是否**随轮次增长**——若第 1 轮干净、第 2/3 轮出现基线残留，
    说明记忆面在轮间没被真正隔离，那么跨轮稳定性读数本身就是假的。
    计数只做整数求和与比率重算，不做平均；n=0 时比率如实为 None。
    """
    counts: dict = {}
    flags: dict = {}
    batches = []
    rows_total = pairs_missing = 0
    for rep in per_batch:
        if rep.get("status") != "computed":
            batches.append(dict(run_id=rep.get("run_id"), status=rep.get("status"),
                                reason=rep.get("reason")))
            continue
        for k, v in (rep.get("attribution_counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
        for k, v in (rep.get("flag_counts") or {}).items():
            flags[k] = flags.get(k, 0) + v
        rows_total += rep.get("tasks_with_both_snapshots") or 0
        pairs_missing += rep.get("tasks_without_pair") or 0
        batches.append(dict(run_id=rep.get("run_id"), status="computed",
                            attribution_counts=rep.get("attribution_counts"),
                            flag_counts=rep.get("flag_counts"),
                            tasks_with_both_snapshots=rep.get("tasks_with_both_snapshots"),
                            tasks_without_pair=rep.get("tasks_without_pair"),
                            review_queue_size=rep.get("review_queue_size"),
                            never_written_share=rep.get("never_written_share"),
                            unattributable_share=rep.get("unattributable_share")))
    computed = [b for b in batches if b.get("status") == "computed"]
    n = rows_total
    residue = counts.get("residue_plus_write", 0) + counts.get("residue_cleared", 0)
    out = dict(status="computed" if computed else "not_applicable",
               batches_computed=len(computed), batches_total=len(batches),
               attribution_counts=counts, flag_counts=flags,
               tasks_with_both_snapshots=n, tasks_without_pair=pairs_missing,
               never_written_share=(round(counts.get("never_written", 0) / n, 4) if n else None),
               unattributable_share=(round((residue + counts.get("capture_gap", 0)) / n, 4)
                                     if n else None),
               batches=batches,
               per_batch_trend={b["run_id"]: (b.get("attribution_counts") or {}).get(
                   "residue_plus_write", 0) + (b.get("attribution_counts") or {}).get(
                   "residue_cleared", 0) for b in computed},
               limits=["聚合是整数求和与比率重算，不做跨批平均",
                       "per_batch_trend 给出每轮残留数：若轮间递增，跨轮稳定性读数本身不可信",
                       "诊断量：不进入评分、不进入排名、不改变 attempt_history"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True, help="跑批目录（含 evidence.jsonl）")
    ap.add_argument("--plan", required=True, help="四层计划（用来识别基线族题目）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    plan = json.loads(pathlib.Path(args.plan).read_text(encoding="utf-8"))
    rep = analyse(pathlib.Path(args.run_dir).resolve(), plan)
    text = json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print("归因报告：", out)
    print(json.dumps({k: rep[k] for k in
                      ("baseline_samples", "tasks_with_both_snapshots", "tasks_without_pair",
                       "attribution_counts", "attribution_rates", "never_written_share",
                       "unattributable_share", "flag_counts", "review_queue_size")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
