#!/usr/bin/env python3
"""okb 引擎 · 分片 run 目录归并（长跑保障：多机/多进程分片 → 单一标准 run 目录）

用法（仓库根目录）：

    python3 -m engine.merge_runs --out runs/run_x runs/run_x.shard1of3 runs/run_x.shard2of3 runs/run_x.shard3of3

语义：
- 校验：分片 manifest/evidence 存在、任务集互斥、suite_version 一致、run_id 一致；
- evidence.jsonl 按分片顺序拼接并**全局重编号** seq/id（行内旧 id 引用一并替换）——
  B 的 verdicts 引用证据 id，因此必须「先归并、后评分」；
- raw/ 复制、tasks.jsonl 拼接、manifest 合并（`merged_from` 留来源）、verdicts.jsonl 置空占位；
- 产物 = 标准 run 目录，`tools/score.py` / `tools/c2_run.py` 零改动消费。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from engine.collectors import now_iso, replace_ids, run_short, sync_raw_payload  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _reject_out_shard_overlap(out_dir: Path, shard_dirs: list[Path]) -> None:
    """ISS-10：输出目录不得与任一分片相等/互相包含——否则 --force 的 rmtree 会吞掉分片。"""
    out_abs = out_dir.resolve()
    for shard in shard_dirs:
        shard_abs = shard.resolve()
        if out_abs == shard_abs:
            raise RuntimeError(f"输出目录与分片目录相同：{out_dir}（拒绝，防止 --force 吞分片）")
        if out_abs in shard_abs.parents:
            raise RuntimeError(f"输出目录是分片目录的上级：{out_dir} ⊃ {shard}")
        if shard_abs in out_abs.parents:
            raise RuntimeError(f"输出目录位于分片目录内部：{out_dir} ⊂ {shard}")


def merge(shard_dirs: list[Path], out_dir: Path, run_id: str | None = None, force: bool = False) -> dict:
    if not shard_dirs:
        raise ValueError("至少需要一个分片目录")
    # ISS-10（momus 复测）：校验一律先于任何删除——原实现 force 的 rmtree 在校验前执行，
    # `--out <某分片> --force` 会先把分片删掉再报参数错（不可逆数据删除类脚枪）。
    _reject_out_shard_overlap(out_dir, shard_dirs)

    manifests: list[dict] = []
    for shard in shard_dirs:
        manifest_path = shard / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"分片缺少 manifest.json：{shard}")
        if not (shard / "evidence.jsonl").exists():
            raise RuntimeError(f"分片缺少 evidence.jsonl：{shard}")
        manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))

    suite_versions = {str(m.get("suite_version")) for m in manifests}
    if len(suite_versions) > 1:
        raise RuntimeError(f"分片 suite_version 不一致：{sorted(suite_versions)}")
    run_ids = {str(m.get("run_id")) for m in manifests}
    if len(run_ids) > 1:
        raise RuntimeError(f"分片 run_id 不一致：{sorted(run_ids)}")
    logical_run_id = run_id or manifests[0].get("run_id") or out_dir.name

    # 任务集互斥校验
    seen: dict[str, str] = {}
    for shard, manifest in zip(shard_dirs, manifests):
        for task in manifest.get("tasks", []):
            tid = str(task.get("task_id"))
            if tid in seen:
                raise RuntimeError(f"任务重复（{seen[tid]} 与 {shard}）：{tid}")
            seen[tid] = str(shard)

    evidence_out = out_dir / "evidence.jsonl"
    if evidence_out.exists() and evidence_out.stat().st_size > 0 and not force:
        raise RuntimeError(f"输出目录已有证据：{evidence_out}（--force 覆盖）")
    if force and out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    short = run_short(str(logical_run_id))

    # 证据归并 + 全局重编号
    seq = 0
    tasks_all: list[dict] = []
    raw_written: set[str] = set()
    with evidence_out.open("w", encoding="utf-8", newline="\n") as out_fh:
        for shard_index, shard in enumerate(shard_dirs):
            lines = _read_jsonl(shard / "evidence.jsonl")
            mapping: dict[str, str] = {}
            for offset, line in enumerate(lines):
                old_id = str(line.get("id", ""))
                new_id = f"ev_{short}_{seq + offset:06d}"
                if old_id:
                    mapping[old_id] = new_id
            raw_sync_pairs: list[tuple[dict, dict]] = []
            for offset, line in enumerate(lines):
                new_line = replace_ids(line, mapping)
                new_line["id"] = mapping.get(str(line.get("id", "")), new_line.get("id"))
                new_line["seq"] = seq + offset
                new_line["run_id"] = logical_run_id
                out_fh.write(json.dumps(new_line, ensure_ascii=False) + "\n")
                raw_sync_pairs.append((line, new_line))
            seq += len(lines)

            # raw/ 复制（任务目录互斥；"run" 伪任务目录（preflight）允许各分片合并）
            shard_raw = shard / "raw"
            if shard_raw.is_dir():
                for task_dir in sorted(shard_raw.iterdir()):
                    if not task_dir.is_dir():
                        continue
                    if task_dir.name == "run":
                        shutil.copytree(task_dir, out_dir / "raw" / "run", dirs_exist_ok=True)
                        continue
                    if task_dir.name in raw_written:
                        raise RuntimeError(f"raw 任务目录重复：{task_dir.name}")
                    shutil.copytree(task_dir, out_dir / "raw" / task_dir.name, dirs_exist_ok=True)
                    raw_written.add(task_dir.name)

            # raw 复制完成后，把重编号影响到的兜底 raw 内容同步为新 payload
            # （否则 raw 内旧 id 引用与 payload 不一致，strict scoped-v2 门误报 raw/payload mismatch）
            for old_line, new_line in raw_sync_pairs:
                sync_raw_payload(out_dir, old_line.get("payload"), new_line.get("payload"),
                                 old_line.get("raw_ref"))

            # 台账拼接
            ledger_path = shard / "tasks.jsonl"
            if ledger_path.exists():
                with (out_dir / "tasks.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                    for line in ledger_path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            fh.write(line + "\n")

            tasks_all.extend(manifests[shard_index].get("tasks", []))

    # verdicts 占位
    (out_dir / "verdicts.jsonl").touch(exist_ok=True)

    # manifest 合并
    base = dict(manifests[0])
    # ISS-09（momus 彩排探针指实的热修）：agents 此前只继承首分片——双产品归并后
    # score 按 agents×samples 枚举时第二个 agent 的行**静默不产**（证据全在、报表无告警）。
    # 名册按 name 并集跨分片聚合；同 agent 多目录（并行实例）保留首份描述。
    _agents_union, _seen = [], set()
    for _m in manifests:
        for _a in _m.get("agents", []) or []:
            if _a.get("name") not in _seen:
                _seen.add(_a.get("name"))
                _agents_union.append(_a)
    base["agents"] = _agents_union
    base["run_id"] = logical_run_id
    base["tasks"] = tasks_all
    base["merged_from"] = [str(shard) for shard in shard_dirs]
    base["merged_at"] = now_iso()
    base["finished_at"] = max((str(m.get("finished_at") or "") for m in manifests), default="")
    stopped = [m.get("stopped_reason") for m in manifests if m.get("stopped_reason")]
    base["stopped_reason"] = stopped[0] if len(stopped) == 1 else (f"merged:{stopped}" if stopped else None)
    base.pop("shard", None)
    (out_dir / "manifest.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # usage 合并
    tokens_total = 0
    per_task: dict[str, dict] = {}
    for shard in shard_dirs:
        usage_path = shard / "usage.json"
        if not usage_path.is_file():
            continue
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
        tokens_total += int(usage.get("tokens_total", 0) or 0)
        per_task.update(usage.get("per_task") or {})
    (out_dir / "usage.json").write_text(
        json.dumps(
            {"run_id": logical_run_id, "tokens_total": tokens_total, "per_task": per_task},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "out": str(out_dir),
        "shards": len(shard_dirs),
        "evidence_lines": seq,
        "tasks": len(tasks_all),
        "tokens_total": tokens_total,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine.merge_runs", description="分片 run 目录归并（先归并后评分）")
    parser.add_argument("shards", nargs="+", help="分片 run 目录（按顺序）")
    parser.add_argument("--out", required=True, help="输出 run 目录（标准目录，供 B 评分）")
    parser.add_argument("--run-id", default=None, help="逻辑 run_id（缺省取分片 manifest）")
    parser.add_argument("--force", action="store_true", help="输出目录已有证据时覆盖")
    args = parser.parse_args(argv)
    try:
        result = merge(
            [Path(p) for p in args.shards],
            Path(args.out),
            run_id=args.run_id,
            force=args.force,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"归并失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
