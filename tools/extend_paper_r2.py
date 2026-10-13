#!/usr/bin/env python3
"""把 r1 正式卷 + 自研「基线/准入」题族合成为 r2 候选卷（只增不改，r1 指纹必须逐字不变）。

为什么要 r2：paper-r1 的检查项 scope 实测为 memory_after 93 / agent_reply 82 / file_final 69 /
**memory_before 0**。缺了任务前基线，"这轮没记住"和"这条从来没被写过"会塌成同一个 `omitted`，
而这两者对记忆系统的诊断意义相反（保留能力 vs 写入/准入能力）。

机制没有动用任何契约改动：`evaluate_at: snapshot_before` 是题卡 schema 已有字段，规则评分器
（tools/scoring/anchors.py）与采集侧（engine/runner.py 每个任务前真的拍一次 before 快照）都已支持，
缺的只是派生器把它映射到 `memory_before` scope——那已在 tools/freeze_candidates.py 修好，本脚本复用
`derive_pilot_checks` 做机械派生，不手写检查项。

诚实边界：
  · 新增题族是**自研题卡**（meta.source=handcrafted），不是从外部卡片派生；矩阵里单列标注。
  · 基线锚点（A3）判的是"任务开始不得已存在某条内容"，属污染判定，不是正确性判据；
    正确性仍由四层语义检查按题面要求判定。失败类只用既有的四个枚举，不新增。
  · r2 是候选卷。是否取代 r1 由队伍决定；本脚本每次都重写 r2，绝不触碰 r1 产物。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import yaml                                          # noqa: E402
from freeze_candidates import derive_pilot_checks    # noqa: E402

R1_YAML = ROOT / "samples/paper-r1.yaml"
R1_JSONL = ROOT / "build/paper-r1.jsonl"
R1_PLAN = ROOT / "samples/four-layer-r1.json"
EXTRA = ROOT / "samples/paper-r2-extra.yaml"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_jsonl(p: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(p: pathlib.Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                 encoding="utf-8", newline="\n")


def build_jsonl(yaml_rel: str, out: pathlib.Path) -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "tools/build_paper.py"), yaml_rel,
                           "-o", str(out)], cwd=str(ROOT), capture_output=True,
                          encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        raise SystemExit("build_paper 失败：%s\n%s" % (proc.stdout[-600:], proc.stderr[-600:]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(ROOT / "build"), help="r2 产物落点（默认 build/）")
    ap.add_argument("--keep-r1-check", action="store_true", default=True)
    args = ap.parse_args(argv)

    before = {p.name: sha(p) for p in (R1_YAML, R1_JSONL, R1_PLAN) if p.is_file()}

    base = yaml.safe_load(R1_YAML.read_text(encoding="utf-8"))
    extra = yaml.safe_load(EXTRA.read_text(encoding="utf-8"))
    ids = {s["id"] for s in base["samples"]}
    clash = sorted(ids & {s["id"] for s in extra["samples"]})
    if clash:
        raise SystemExit("r2 新增题与 r1 撞 id：%s" % clash[:5])

    merged = dict(suite_version="v2.0",
                  samples=list(base["samples"]) + sorted(extra["samples"], key=lambda s: s["id"]))
    r2_yaml = ROOT / "samples/paper-r2.yaml"
    header = ("# samples/paper-r2.yaml —— r2 **候选**卷：paper-r1 的 100 题逐字原样 + 自研基线/准入题族\n"
              "# 生成方式：tools/extend_paper_r2.py（可重跑，删除后重建一致）。\n"
              "# r1 仍是当前交付卷；r2 的存在意义是把 memory_before 从 0 补起来，见\n"
              "# samples/paper-r2-extra.yaml 头部说明。本文件里的前 100 题与 samples/paper-r1.yaml 同字节。\n")
    r2_yaml.write_text(header + yaml.safe_dump(merged, allow_unicode=True, sort_keys=False,
                                               default_flow_style=False, width=100000),
                       encoding="utf-8", newline="\n")

    out_dir = pathlib.Path(args.out_dir)
    r2_jsonl = out_dir / "paper-r2.jsonl"
    build_jsonl("samples/paper-r2.yaml", r2_jsonl)
    rows = read_jsonl(r2_jsonl)
    by_id = {r["id"]: r for r in rows}
    extra_ids = sorted(s["id"] for s in extra["samples"])
    missing = [i for i in extra_ids if i not in by_id]
    if missing:
        raise SystemExit("编译后丢了新增题：%s" % missing)

    r1_plan = json.loads(R1_PLAN.read_text(encoding="utf-8"))
    new_samples, gaps = [], []
    for sid in extra_ids:
        checks, gap, disposition = derive_pilot_checks(by_id[sid])
        new_samples.append(dict(sample_id=sid, checks=checks, anchor_disposition=disposition))
        gaps.extend(gap)
    plan = dict(format=r1_plan["format"], paper_sha256=sha(r2_jsonl),
                samples=list(r1_plan["samples"]) + new_samples)
    r2_plan = ROOT / "samples/four-layer-r2.json"
    r2_plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")

    # 用评分侧自己的校验器验证（不是我自己写的断言）
    from scoring.four_layer import validate_plan
    validate_plan(plan, rows, sha(r2_jsonl))

    import collections
    scope = collections.Counter()
    for s in plan["samples"]:
        for c in s["checks"]:
            # 客观工件类检查项按 schema 不带 scope（只有 op/path），实测 r1 有 51 条这种行
            key = c.get("scope") or ("objective:" + str(c.get("op") or "?"))
            scope[key] += 1
    after = {p.name: sha(p) for p in (R1_YAML, R1_JSONL, R1_PLAN) if p.is_file()}
    drift = {k: (before[k], after[k]) for k in before if before[k] != after.get(k)}

    summary = dict(format="okb-paper-r2-summary-v1",
                   r2_paper=str(r2_yaml), r2_jsonl=str(r2_jsonl), r2_plan=str(r2_plan),
                   samples=len(rows), plan_rows=len(plan["samples"]),
                   added_samples=extra_ids, added_checks=sum(len(s["checks"]) for s in new_samples),
                   scope_counts=dict(scope),
                   anchor_gaps=len(gaps),
                   paper_sha256=sha(r2_jsonl), plan_sha256=sha(r2_plan),
                   r1_untouched=not drift, r1_drift=drift,
                   r1_paper_sha=before.get("paper-r1.yaml"),
                   r1_jsonl_sha=before.get("paper-r1.jsonl"),
                   r1_plan_sha=before.get("four-layer-r1.json"))
    note = ROOT / "notes/paper-r2-candidate.json"
    note.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    summary["credential"] = str(note)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not drift else 2


if __name__ == "__main__":
    raise SystemExit(main())
