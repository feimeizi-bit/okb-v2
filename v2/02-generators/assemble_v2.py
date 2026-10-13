"""WP2 装配器：四部件 → paper-v2.0 + 四层计划（绑 sha）→ validate_plan。

部件：stock-v2（68）+ sample-template.fixed（1）+ new-items-du.fixed（9）+ new-items-rest.fixed（14）= 92
处理：
  A. 剥离 expect.trap → trap-ledger.json（v2 由计划检查承接失败形态，避免 W206）
  B. scope 规范化：file_after→file_final(+path)；reply→agent_reply(+session/turn=probe)；memory_after 原样
  C. anchor_disposition：新题按"锚点类型×检查scope"映射生成；存量继承 dev2 并修补（移除/新增锚点）
  D. 组装 plan（format=okb-four-layer-v1）→ validate_plan → 全卷 lint
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "01-paper-v2"
REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "tools"), str(REPO)]

PARTS = [("stock-v2", "stock"), ("sample-template.fixed", "new"),
         ("new-items-du.fixed", "new"), ("new-items-rest.fixed", "new")]

# QC 修复：补内容错（confused）判据——仅针对质检发现的 2 个缺面样本
EXTRA_CHECKS = {
    "recall_coffee_001": [{
        "id": "QC_CONF_ROBUST", "layer": "semantic", "scope": "reply",
        "requirement": "回复若明确给出加糖/正常糖/少糖（违反“不加糖”偏好）=confused；正确=给出不加糖的点单。",
        "on_failure": "confused", "basis": "s1.t1"}],
    "reuse_memory_only_001": [{
        "id": "QC_CONF_ROBUST", "layer": "semantic", "scope": "file_final", "path": "周报.md",
        "requirement": "周报三节若为通用模板话术、与记忆素材内容矛盾或对象写错=confused；正确=按素材内容三节展开。",
        "on_failure": "confused", "basis": "s1.t1"}],
}


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def probe_session(sample):
    sess = [s for s in sample["script"] if s.get("phase") == "probe"]
    s = (sess[-1] if sess else sample["script"][-1])
    return s["session"]


def resolve_path(check, sample):
    names = []
    for a in sample.get("anchors", []):
        if a.get("path"):
            names.append(a["path"])
    for f in sample.get("fixture", []):
        names.append(f["path"])
    req = check["requirement"]
    for n in names:
        if n in req:
            return n
    for n in names:
        stem = re.sub(r"\.[a-z0-9]+$", "", n)
        if stem and stem in req:
            return n
    uniq = sorted(set(names))
    if len(uniq) == 1:
        return uniq[0]
    raise ValueError("cannot resolve file path for %s / %s" % (sample["id"], check["id"]))


def canon_check(c, sample):
    c = dict(c)
    if c.get("layer") != "semantic":
        return c
    if c["scope"] == "file_after":
        c["scope"] = "file_final"
        if not c.get("path"):
            c["path"] = resolve_path(c, sample)
    elif c["scope"] == "reply":
        c["scope"] = "agent_reply"
        c["session"] = probe_session(sample)
        c["turn"] = 1
    return c


def disp_for_new(sample, checks):
    disp, used = {}, set()
    anchors = sample["anchors"]
    for a in anchors:
        carried = []
        for c in checks:
            if c["scope"].startswith("memory_") and a["type"] in ("memory_entry", "memory_read"):
                carried.append(c["id"])
            elif c["scope"] == "file_final" and a["type"] == "file_content" and a.get("path") == c.get("path"):
                carried.append(c["id"])
            elif c["scope"] == "agent_reply" and a["type"] == "reply_text":
                carried.append(c["id"])
        if carried:
            disp[a["id"]] = {"role": "semantic", "checks": sorted(set(carried)),
                             "reason": "v2 结构/内容锚点承接如下语义检查（映射=类型×scope）"}
            used |= set(carried)
    for c in checks:
        if c["id"] in used:
            continue
        want = (("memory_entry", "memory_read") if c["scope"].startswith("memory_")
                else ("file_content",) if c["scope"] == "file_final" else ("reply_text",))
        for a in anchors:
            if a["type"] in want:
                d = disp.setdefault(a["id"], {"role": "semantic", "checks": [], "reason": "兜底映射"})
                d["checks"] = sorted(set(d["checks"] + [c["id"]]))
                used.add(c["id"])
                break
        else:
            raise ValueError("no anchor to carry %s / %s" % (sample["id"], c["id"]))
    for a in anchors:
        if a["id"] not in disp:
            disp[a["id"]] = {"role": "diagnostic", "checks": [],
                             "reason": "v2 结构护栏/防空转；无独立判据，语义由其他锚点承接"}
    fix_roles(disp, checks)
    return disp


def disp_for_stock(sample, checks, base):
    ids = {a["id"] for a in sample["anchors"]}
    disp = {k: json.loads(json.dumps(v)) for k, v in base.items() if k in ids}
    for a in sample["anchors"]:
        if a["id"] not in disp:
            disp[a["id"]] = {"role": "diagnostic", "checks": [],
                             "reason": "v2 修复新增结构护栏；语义由其余锚点承接"}
    used = {x for v in disp.values() for x in v["checks"]}
    for c in checks:
        if c["id"] in used:
            continue
        k = next(iter(disp))
        disp[k]["checks"] = sorted(set(disp[k]["checks"] + [c["id"]]))
        used.add(c["id"])
    fix_roles(disp, checks)
    return disp


def fix_roles(disp, checks):
    by = {c["id"]: c for c in checks}
    for v in disp.values():
        if v["role"] == "diagnostic":
            if v["checks"]:
                v["role"] = "semantic"
            continue
        layers = {by[x]["layer"] for x in v["checks"]}
        v["role"] = "semantic" if layers == {"semantic"} else "objective" if layers == {"objective"} else "mixed"


def main():
    samples, trap_ledger, checks_by, disp_by, stock_disp = [], {}, {}, {}, {}
    dev2 = load_json(REPO / "samples" / "four-layer-r1-dev2.json")
    dev2_by = {s["sample_id"]: s for s in dev2["samples"]}
    for stem, kind in PARTS:
        y = yaml.safe_load((PAPER / (stem + ".yaml")).read_text(encoding="utf-8"))
        frag = load_json(PAPER / (stem.replace(".fixed", "") + ".plan.json"))
        frag_by = {r["sample_id"]: r["checks"] for r in frag["samples"]}
        for s in y["samples"]:
            sid = s["id"]
            exp = s.get("expect") or {}
            if exp.get("trap") is not None:
                trap_ledger[sid] = exp.pop("trap")
                s.setdefault("meta", {})["limitation"] = (
                    str(s["meta"].get("limitation", "")) + "（v2：trap 字段入台账，失败形态由计划检查承接）").strip()
            checks = ([canon_check(c, s) for c in frag_by.get(sid, [])]
                      + [canon_check(c, s) for c in EXTRA_CHECKS.get(sid, [])])
            if not checks:
                raise ValueError("no checks for " + sid)
            checks_by[sid] = checks
            if kind == "stock":
                disp_by[sid] = disp_for_stock(s, checks, dev2_by[sid]["anchor_disposition"])
            else:
                disp_by[sid] = disp_for_new(s, checks)
            samples.append(s)

    assert len({s["id"] for s in samples}) == len(samples) == 92, len(samples)

    header = ("# paper-v2.yaml —— v2 正式卷 v2.0（92 题：存量修复 68 + 新著 24）\n"
              "# 生成：02-generators/assemble_v2.py；构件与修复台账见同目录\n")
    (PAPER / "paper-v2.yaml").write_text(
        header + yaml.safe_dump({"suite_version": "v2.0", "samples": samples},
                                allow_unicode=True, sort_keys=False, width=400), encoding="utf-8")
    (PAPER / "trap-ledger.json").write_text(json.dumps(trap_ledger, ensure_ascii=False, indent=1), encoding="utf-8")

    r = subprocess.run([sys.executable, str(REPO / "tools" / "build_paper.py"),
                        str(PAPER / "paper-v2.yaml"), "-o", str(PAPER / "_paperv2.jsonl")],
                       cwd=REPO, encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    (PAPER / "_paperv2-build.txt").write_text(r.stdout, encoding="utf-8")
    if r.returncode != 0:
        print(r.stdout[-3000:])
        raise SystemExit("build failed")
    import hashlib
    sha = hashlib.sha256((PAPER / "_paperv2.jsonl").read_bytes()).hexdigest()

    plan_rows = [{"sample_id": s["id"], "anchor_disposition": disp_by[s["id"]],
                  "checks": checks_by[s["id"]]} for s in samples]
    plan = {"format": "okb-four-layer-v1", "paper_sha256": sha, "samples": plan_rows}
    (PAPER / "paper-v2.plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = [json.loads(l) for l in (PAPER / "_paperv2.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    from scoring.four_layer import validate_plan
    validate_plan(plan, rows, sha)
    print("VALIDATE_PLAN OK | samples:", len(plan_rows),
          "| checks:", sum(len(x["checks"]) for x in plan_rows),
          "| sha:", sha[:16])
    print("WROTE paper-v2.yaml / paper-v2.plan.json / trap-ledger.json / _paperv2.jsonl")


if __name__ == "__main__":
    main()
