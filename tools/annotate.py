#!/usr/bin/env python3
"""人工标注工具：旧 make-worklist 仅为判官同源的开发校准视图，
不是无规则提示的独立盲评。新 make-blind-worklist 生成独立证据草稿包。

用法：
  python tools/annotate.py make-worklist --run-dirs runs/a,runs/b --paper build/paper-v0.2.jsonl \
      --out build/annotate/worklist.json [--max-rows N]
  # 标注员复制 worklist.rows[].digest 看，产出 labels.json：{"row_key","label","note"}
  python tools/annotate.py validate --worklist w.json --labels my.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scoring import anchors as A                       # noqa: E402
from scoring.index import EvidenceIndex, InputErrorLocal  # noqa: E402
from scoring.judge_client import build_visible_pack    # noqa: E402
from loader import load_jsonl                          # noqa: E402

TIERS5 = {"remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"}


def _soft_outcomes(sample, idx, task_id):
    outs = []
    for a in sample.get("anchors", []):
        try:
            o = A.evaluate(sample, a, idx, task_id)
        except Exception:  # noqa: BLE001 —— 求值失败的锚不进摘要（判官同样拿不到）
            continue
        if o.severity == "soft" or o.result in ("skip",):
            outs.append(o)
    return outs


def make_worklist(run_dirs, paper_path, out, max_rows):
    if max_rows < 0:
        print("max_rows 必须 >=0（0=全部），未写出标注包", file=sys.stderr)
        return 1
    paper = {s["id"]: s for s in load_jsonl(paper_path)}
    rows = []
    for rd in run_dirs:
        vj = os.path.join(rd, "verdicts.jsonl")
        if not os.path.exists(vj):
            continue
        tier = os.path.basename(rd.rstrip("/\\"))
        idx = EvidenceIndex.load(rd)
        for line in Path(vj).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            s = paper.get(r["sample_id"])
            if s is None:
                continue
            # G3（恶意评审指实）：旧框「只取 remembered」把负判定按构造排除，
            # κ 只在正类上度量=自证循环。新框：**五类判决一律可入样**，条件只剩
            # ①status=ok（skip/env 行判官无对象）②存在 soft 清单（判官有裁决权）。
            # hard 已定案且无 soft 的行归影子三对照道（judge_drift 工具），不混 κ。
            if r.get("status") != "ok":
                continue
            outs = _soft_outcomes(s, idx, r["task_id"])
            if not outs:
                continue
            _, parts = build_visible_pack(s, outs, idx, r["task_id"])
            # row_key 不携带判决类别；digest 仍为判官同源包（非独立盲评原始证据）。
            rows.append({"row_key": f"R{len(rows)+1:02d}", "digest": parts,
                         "_vclass": r.get("verdict") or "?",
                         "_meta_tier": tier, "_meta_sample": r["sample_id"],
                         # review P2 补漏：r1/r2 同 (tier,sample) 多行时，keymap 必须可指认
                         # 到轮次（repeat 一致性分析与 live 判官对位全靠它）
                         "_meta_task": r.get("task_id") or r["sample_id"]})
    if not rows:
        print("无可用标注行（status=ok 且有 soft/skip 锚点），未写出标注包", file=sys.stderr)
        return 1
    # 按类轮转：小类耗尽后将剩余配额分给其他类，严格不超过 max_rows。
    # 上限小于实际类数时不能声称覆盖全部；全量框从未包含的类也不凭空补造。
    order = lambda x: (x["_meta_tier"], x["_meta_sample"], x["_meta_task"])
    rows.sort(key=order)
    if max_rows:
        from collections import deque
        pools = {}
        for row in rows:
            pools.setdefault(row["_vclass"], deque()).append(row)
        if max_rows < len(pools):
            print("WARNING: max_rows 小于实际类别数，本包不覆盖所有类别", file=sys.stderr)
        kept = []
        while len(kept) < min(max_rows, len(rows)):
            for label in sorted(pools):
                if pools[label] and len(kept) < max_rows:
                    kept.append(pools[label].popleft())
        rows = sorted(kept, key=order)  # 不按判决类排序发给标注员
    for i, r in enumerate(rows):            # 统一重编号（截断后连续）
        r["row_key"] = f"R{i+1:02d}"
    # 兼容：消费方按 [0]=tier [1]=sample 位取；task 为第三位（老 reader 不破坏）
    for r in rows:
        r.pop("_vclass", None)   # 判决类不入标注包（防「规则已判 X」泄漏给标注员）
    mapping = {r["row_key"]: [r.pop("_meta_tier"), r.pop("_meta_sample"), r.pop("_meta_task")]
               for r in rows}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)   # 修（09-24 T0 实操抓出）：
    with open(os.path.join(os.path.dirname(out) or ".", "keymap.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(mapping, fh, ensure_ascii=False, indent=1)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"purpose": "development-calibration", "view_version": "judge-shared-legacy",
                   "n_rows": len(rows), "rows": rows}, fh, ensure_ascii=False, indent=1)
    print(f"worklist: {len(rows)} 行 → {out}")
    return 0


def validate(worklist, labels):
    with open(worklist, encoding="utf-8") as fh:
        work = json.load(fh)
    if work.get("purpose") == "independent-human-draft":
        print("独立包必须用 validate-blind（要求 package_id 和内容哈希绑定）", file=sys.stderr)
        return 1
    keys = {r["row_key"] for r in work["rows"]}
    bad = []
    seen = set()
    for line in open(labels, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["label"] not in TIERS5:
            bad.append(f"非法 label: {r}")
        if r["row_key"] not in keys:
            bad.append(f"未知 row_key: {r['row_key']}")
        if r["row_key"] in seen:
            bad.append(f"重复: {r['row_key']}")
        seen.add(r["row_key"])
    miss = keys - seen
    print(f"labels {len(seen)}/{len(keys)}，非法 {len(bad)}，缺失 {len(miss)}")
    for b in bad[:8]:
        print("  ✗", str(b)[:120])
    if miss:
        print("  缺:", sorted(miss)[:8])
    return 1 if (bad or miss) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-worklist")
    m.add_argument("--run-dirs", required=True)
    m.add_argument("--paper", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--max-rows", type=int, default=0)
    v = sub.add_parser("validate")
    v.add_argument("--worklist", required=True)
    v.add_argument("--labels", required=True)
    b = sub.add_parser("make-blind-worklist", help="独立人工证据草稿，不读取verdicts")
    b.add_argument("--selection", required=True, help="私有JSON rows: run_dir/sample_id/task_id")
    b.add_argument("--paper", required=True)
    b.add_argument("--public-dir", required=True, help="必须是新目录")
    b.add_argument("--private-dir", required=True, help="必须是独立且非嵌套的新目录")
    bv = sub.add_parser("validate-blind")
    bv.add_argument("--worklist", required=True)
    bv.add_argument("--labels", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "make-worklist":
        print("WARNING: 开发校准视图含规则提示，不是独立人工盲评", file=sys.stderr)
        return make_worklist(args.run_dirs.split(","), args.paper, args.out, args.max_rows)
    if args.cmd in ("make-blind-worklist", "validate-blind"):
        from blind_annotation import make_package, validate_labels, BlindError
        try:
            if args.cmd == "make-blind-worklist":
                work = make_package(args.selection, args.paper, args.public_dir, args.private_dir)
                print(f"独立证据草稿 {work['n_rows']} 行；须人工审核后才能发放")
                return 0
            errors = validate_labels(args.worklist, args.labels)
            for error in errors[:10]:
                print(error, file=sys.stderr)
            print(f"标签结构检查：{len(errors)} 个错误（不证明标注正确或独立）")
            return int(bool(errors))
        except (BlindError, InputErrorLocal, OSError, ValueError, KeyError, TypeError) as exc:
            # Do not print input text or sensitive paths in public-facing errors.
            print(f"独立包输入/输出检查失败：{type(exc).__name__}: {exc}；未批准发放", file=sys.stderr)
            return 2
    return validate(args.worklist, args.labels)


if __name__ == "__main__":
    sys.exit(main())
