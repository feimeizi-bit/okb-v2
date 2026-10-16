#!/usr/bin/env python3
"""一次性修复：把重编号后 stale 的兜底 raw 文件内容同步回当前 payload。

背景：`--retry-invalid`（collectors.remove_tasks）与 `merge_runs` 会对 evidence.jsonl
全局重编号，并同步替换行内的旧 id 引用；但 raw/ 下的兜底 JSONL 是写盘时的历史字节，
未同步 → strict scoped-v2 证据门报 `raw/payload mismatch`（实测 201/273 openclaw 任务
被挡）。2026-10-11 起引擎在手术/归并时同步 raw；本工具用于修复此前的存量 run。

口径：只重写「单行 JSONL 且键集与 payload 一致」的 raw 文件（内容差异=旧 id 漂移）；
文本 raw / 多行 raw / 键集不一致的一律不动并计入 skipped。默认只报告，`--apply` 才写盘。
"""
from __future__ import annotations

import argparse
import json
import pathlib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--apply", action="store_true", help="实际写盘；缺省仅报告")
    args = ap.parse_args(argv)

    run = pathlib.Path(args.run_dir)
    ev = run / "evidence.jsonl"
    if not ev.is_file():
        print("缺少 evidence.jsonl：%s" % ev)
        return 2

    rows = [json.loads(line) for line in ev.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 共享 raw 路径预扫描：system_reset / system_time_normalization 等文件被同一任务的多行
    # 引用并相互覆盖（每行 payload 各自不同），不是重编号漂移——一律不动。
    ref_count: dict[str, int] = {}
    for row in rows:
        ref = row.get("raw_ref") or {}
        rel = str(ref.get("path") or "")
        if rel.startswith("raw/"):
            ref_count[rel] = ref_count.get(rel, 0) + 1

    would = applied = same = skipped = missing = 0
    shared = 0
    diff_fields: dict[str, int] = {}
    shared_paths: dict[str, int] = {}
    for row in rows:
        ref = row.get("raw_ref") or {}
        try:
            if int(ref.get("line", 1) or 1) != 1:
                continue
        except (TypeError, ValueError):
            continue
        rel = str(ref.get("path") or "")
        if not rel.startswith("raw/"):
            continue
        path = run / rel
        if not path.is_file():
            missing += 1
            continue
        try:
            objs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            skipped += 1
            continue
        payload = row.get("payload")
        if len(objs) != 1:
            skipped += 1
            continue
        if objs[0] == payload:
            same += 1
            continue
        if not isinstance(payload, dict) or set(objs[0].keys()) != set(payload.keys()):
            skipped += 1
            continue
        if ref_count.get(rel, 0) > 1:
            shared += 1
            shared_paths[rel.split("/")[-1]] = shared_paths.get(rel.split("/")[-1], 0) + 1
            continue
        for key in objs[0]:
            if objs[0].get(key) != payload.get(key):
                diff_fields[key] = diff_fields.get(key, 0) + 1
        would += 1
        if args.apply:
            path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            applied += 1

    print(json.dumps({
        "run_dir": str(run), "mode": "apply" if args.apply else "dry-run",
        "would_heal": would, "applied": applied,
        "unchanged": same, "skipped": skipped, "raw_missing": missing,
        "shared_path_skipped": shared, "shared_top": dict(sorted(shared_paths.items(), key=lambda kv: -kv[1])[:6]),
        "diff_fields_top": dict(sorted(diff_fields.items(), key=lambda kv: -kv[1])[:10]),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
