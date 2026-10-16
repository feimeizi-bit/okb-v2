"""assertion-bench：断言实现评测与晋升选择（v0 骨架）。

输入（JSONL，每槽位每实现一行）：
  --labels   [{slot_id, truth: supported|contradicted|insufficient}]
  --results  [{slot_id, impl_id, result: supported|contradicted|insufficient}]
  --registry registry-v2.json（槽位 → 实现清单/引擎/状态）
输出（新建目录）：
  report.json / summary.md / promotions.json（建议不自动生效）

指标（实现维度聚合，跨槽位）：
  覆盖率 = decided / applicable（applicable=有真值行或结果行的槽位）
  FP 误杀率 = (结果=违反 ∧ 真值=支持) / decided
  FN 漏判率 = (结果=支持 ∧ 真值=违反) / decided
选择（字典序）：硬门(decided≥min ∧ FP≤5% ∧ FN≤5%) → 覆盖率最大 → FP+FN 最小 → 规则优先 → impl_id。
自测：--selftest 内置合成语料。
"""
import argparse
import json
from pathlib import Path

GATES = {"max_fp": 0.05, "max_fn": 0.05, "min_decided": 3}


def evaluate(labels, results, registry):
    truth = {}
    for r in labels:
        truth.setdefault(r["slot_id"], r["truth"])
    res = {}
    for r in results:
        key = (r["slot_id"], r["impl_id"])
        if key in res:
            raise ValueError("duplicate result row: %s" % (key,))
        res[key] = r["result"]
    impls = {}
    for s in registry["slots"]:
        impls[s["slot_id"]] = [(i["impl_id"], i.get("engine", "?"), i.get("status", "candidate"))
                               for i in s["implementations"]]

    agg = {}
    slots_all = set(truth) | {k[0] for k in res}
    for slot in sorted(slots_all):
        ts = truth.get(slot)
        for impl_id, engine, status in impls.get(slot, []):
            a = agg.setdefault(impl_id, dict(engine=engine, status=status, applicable=0,
                                             decided=0, fp=0, fn=0, insuff_truth=0))
            if ts is not None:
                a["applicable"] += 1
            r = res.get((slot, impl_id))
            decided = r in ("supported", "contradicted")
            if decided:
                a["decided"] += 1
            if ts == "insufficient":
                a["insuff_truth"] += 1
                continue
            if ts in ("supported", "contradicted") and decided:
                if r == "contradicted" and ts == "supported":
                    a["fp"] += 1
                if r == "supported" and ts == "contradicted":
                    a["fn"] += 1

    for impl_id, a in agg.items():
        a["coverage"] = a["decided"] / a["applicable"] if a["applicable"] else None
        a["fp_rate"] = a["fp"] / a["decided"] if a["decided"] else None
        a["fn_rate"] = a["fn"] / a["decided"] if a["decided"] else None
        a["gate_ok"] = bool(
            a["decided"] >= GATES["min_decided"]
            and (a["fp_rate"] or 0) <= GATES["max_fp"]
            and (a["fn_rate"] or 0) <= GATES["max_fn"])

    per_slot, promotions = {}, []
    for slot in sorted(impls):
        rows = []
        for impl_id, engine, status in impls[slot]:
            a = agg.get(impl_id)
            if a:
                rows.append(dict(impl_id=impl_id, status=status,
                                 engine=a["engine"], coverage=a["coverage"],
                                 fp_rate=a["fp_rate"], fn_rate=a["fn_rate"],
                                 decided=a["decided"], gate_ok=a["gate_ok"]))
        per_slot[slot] = rows
        oks = [x for x in rows if x["gate_ok"]]
        incumb = next((x for x in rows if x["status"] == "incumbent"), None)
        if oks:
            winner = sorted(oks, key=lambda x: (-(x["coverage"] or 0),
                                                (x["fp_rate"] or 0) + (x["fn_rate"] or 0),
                                                0 if x["engine"] == "rule" else 1,
                                                x["impl_id"]))[0]
            if incumb and winner["impl_id"] != incumb["impl_id"]:
                promotions.append({"slot_id": slot, "from": incumb["impl_id"],
                                   "to": winner["impl_id"], "auto_applied": False,
                                   "reason": "candidate meets gates and outranks incumbent"})
        elif incumb and not incumb["gate_ok"]:
            promotions.append({"slot_id": slot, "from": incumb["impl_id"], "to": None,
                               "auto_applied": False,
                               "reason": "incumbent fails gates and no qualified candidate"})

    macro = {}
    dec = sum(a["decided"] for a in agg.values())
    if dec:
        macro = {"decided": dec,
                 "fp_rate": sum(a["fp"] for a in agg.values()) / dec,
                 "fn_rate": sum(a["fn"] for a in agg.values()) / dec}
    report = {"format": "okb-assertion-bench-report-v0", "gates": GATES,
              "impl_metrics": {k: {kk: vv for kk, vv in v.items() if kk != "status"}
                               for k, v in sorted(agg.items())},
              "slots": per_slot, "macro": macro}
    return report, promotions


def synth():
    labels, results = [], []
    # 5 个 ok 槽位：3 支持 + 2 违反
    for i in range(5):
        labels.append({"slot_id": "s_ok_%d:R_X" % i,
                       "truth": "supported" if i < 3 else "contradicted"})
    # impl:A（现役 rule）：1 误杀 + 1 漏判 → 双门都破
    for i in range(5):
        results.append({"slot_id": "s_ok_%d:R_X" % i, "impl_id": "impl:A",
                        "result": "contradicted" if i in (0, 3) else "supported"})
    # impl:B（候选 semantic）：全对
    for i in range(5):
        results.append({"slot_id": "s_ok_%d:R_X" % i, "impl_id": "impl:B",
                        "result": "contradicted" if i >= 3 else "supported"})
    # 4 个 bad 槽位：2 支持 + 2 违反；impl:C 全答 supported → 漏判
    for i in range(4):
        labels.append({"slot_id": "s_bad_%d:R_Y" % i,
                       "truth": "contradicted" if i >= 2 else "supported"})
    for i in range(4):
        results.append({"slot_id": "s_bad_%d:R_Y" % i, "impl_id": "impl:C", "result": "supported"})
    registry = {"slots": [
        {"slot_id": "s_ok_%d:R_X" % i, "implementations": [
            {"impl_id": "impl:A", "engine": "rule", "status": "incumbent"},
            {"impl_id": "impl:B", "engine": "semantic", "status": "candidate"}]}
        for i in range(5)
    ] + [
        {"slot_id": "s_bad_%d:R_Y" % i, "implementations": [
            {"impl_id": "impl:C", "engine": "rule", "status": "incumbent"}]}
        for i in range(4)
    ]}
    return labels, results, registry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry")
    ap.add_argument("--labels")
    ap.add_argument("--results")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        labels, results, registry = synth()
    else:
        labels = [json.loads(l) for l in Path(a.labels).read_text(encoding="utf-8").splitlines() if l.strip()]
        results = [json.loads(l) for l in Path(a.results).read_text(encoding="utf-8").splitlines() if l.strip()]
        registry = json.loads(Path(a.registry).read_text(encoding="utf-8"))
    report, promotions = evaluate(labels, results, registry)
    out = Path(a.out or str(Path(__file__).resolve().parents[1] / "runs" / "selftest"))
    if out.exists():
        raise SystemExit("out must be new: " + str(out))
    out.mkdir(parents=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "promotions.json").write_text(json.dumps(promotions, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# assertion-bench 报告（自测）" if a.selftest else "# assertion-bench 报告", "",
             "- macro: " + json.dumps(report["macro"], ensure_ascii=False),
             "- impl 指标: " + json.dumps(report["impl_metrics"], ensure_ascii=False), "",
             "## 晋升建议（不自动生效）"]
    for p in promotions[:12]:
        lines.append("- `%s`: %s → %s — %s" % (p["slot_id"], p["from"], p["to"] or "（无合格候选）", p["reason"]))
    if len(promotions) > 12:
        lines.append("- …共 %d 条，见 promotions.json" % len(promotions))
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("WROTE", out)
    print(json.dumps({"macro": report["macro"], "promotions": len(promotions)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
