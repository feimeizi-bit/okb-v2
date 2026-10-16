"""v2 出题转换器：把「作者稿」（带 plan_checks/limitation/deep_slots 扩展）转换为
- *.fixed.yaml      —— 通过 build_paper schema 的卷文件
- *.plan.json       —— 四层计划片段（sample_id + checks），由 WP2 生成器并入正式 plan

转换规则：
1) expect.plan_checks → sidecar（schema 禁 additionalProperties）
2) anchors[].limitation → 并入 anchors[].desc（W207 豁免说明）
3) meta.deep_slots → 样本顶层 tags（与既有 time_anchor/strong_disturb/multi_step 一致）
4) 数字型 content_regex 转字符串
"""
import json
import sys
from pathlib import Path

import yaml

def convert(src: Path) -> tuple[Path, Path]:
    d = yaml.safe_load(src.read_text(encoding="utf-8"))
    plan_rows = []
    for s in d.get("samples", []):
        expect = s.get("expect") or {}
        checks = expect.pop("plan_checks", None) or []
        if checks:
            plan_rows.append({"sample_id": s["id"], "checks": checks})
        meta = s.get("meta") or {}
        ds = meta.pop("deep_slots", None)
        if ds:
            tags = list(s.get("tags") or [])
            for t in (ds if isinstance(ds, list) else [ds]):
                if t not in tags:
                    tags.append(t)
            s["tags"] = tags
        for a in s.get("anchors") or []:
            lim = a.pop("limitation", None)
            if lim:
                a["desc"] = (str(a.get("desc", "")) + "（" + str(lim) + "）").strip()
            for k in ("content_regex", "regex", "value_regex"):
                if isinstance(a.get(k), int):
                    a[k] = str(a[k])
    out_yaml = src.with_suffix(".fixed.yaml")
    out_plan = src.with_suffix(".plan.json")
    out_yaml.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False, width=400),
                        encoding="utf-8")
    out_plan.write_text(json.dumps({"format": "okb-four-layer-plan-fragment-v2",
                                    "samples": plan_rows}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return out_yaml, out_plan

if __name__ == "__main__":
    for p in sys.argv[1:]:
        y, j = convert(Path(p))
        print("WROTE", y.name, "|", j.name)
