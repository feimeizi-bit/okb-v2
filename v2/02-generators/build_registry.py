"""WP2 扩展：从 v2 作者稿生成断言库 registry。

输入：01-paper-v2/{sample-template,new-items-du,new-items-rest}.fixed.yaml（卷）
     01-paper-v2/*.plan.json（计划片段）
输出：05-assertions/registry-v2.json
     槽位 = 计划检查（semantic）+ 锚点（rule）；每个槽位带初版实现（incumbent, metrics=null）。
"""
import json
from pathlib import Path

import yaml

PAPER = Path(__file__).resolve().parents[1] / "01-paper-v2"
OUT = Path(__file__).resolve().parents[1] / "05-assertions" / "registry-v2.json"
STEMS = ("stock-v2", "sample-template", "new-items-du", "new-items-rest")


def main() -> int:
    slots = []
    yfile = PAPER / "paper-v2.yaml"
    pfile = PAPER / "paper-v2.plan.json"
    y = yaml.safe_load(yfile.read_text(encoding="utf-8"))
    plan = json.loads(pfile.read_text(encoding="utf-8"))
    checks_by = {r["sample_id"]: r["checks"] for r in plan["samples"]}
    for s in y["samples"]:
        sid = s["id"]
        for c in checks_by.get(sid, []):
            slots.append({
                "slot_id": f"{sid}:{c['id']}", "kind": "plan_check",
                "sample_id": sid, "dimension": s["dimension"],
                "layer": c.get("layer"), "scope": c.get("scope"),
                "requirement": c.get("requirement"), "on_failure": c.get("on_failure"),
                "implementations": [{
                    "impl_id": "semantic:judge-default" if c.get("layer") == "semantic" else "rule:objective-declared",
                    "engine": "semantic" if c.get("layer") == "semantic" else "rule",
                    "status": "incumbent", "spec": {"src": "paper-v2.plan.json"},
                    "metrics": None}],
            })
        for a in s.get("anchors") or []:
            spec = {k: v for k, v in a.items()
                    if k not in ("id", "type", "desc", "severity", "on_violation", "expect")}
            slots.append({
                "slot_id": f"{sid}:{a['id']}", "kind": "anchor",
                "sample_id": sid, "dimension": s["dimension"],
                "anchor_type": a["type"], "severity": a["severity"],
                "expect": a["expect"], "on_violation": a.get("on_violation"),
                "implementations": [{
                    "impl_id": "rule:anchor-declared", "engine": "rule",
                    "status": "incumbent", "spec": spec, "metrics": None}],
            })
    reg = {
        "format": "okb-assertion-registry-v2", "version": "v2.0-draft1",
        "generated": "2026-10-13",
        "paper_files": ["paper-v2.yaml", "paper-v2.plan.json"],
        "slots": slots,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    print("WROTE", OUT, "| slots:", len(slots),
          "| plan_checks:", sum(1 for s in slots if s["kind"] == "plan_check"),
          "| anchors:", sum(1 for s in slots if s["kind"] == "anchor"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
