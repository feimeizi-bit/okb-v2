#!/usr/bin/env python3
"""okb · C-2 离线闭环 harness（协议 = notes/c2-protocol-2026-09-21.md，判据与矩阵已锁定）。

流程：五档 simulate → 五档 score → 逐格比对预注册矩阵 → 核心对称集单调性 →
      逐 hard 锚点消融（因果力） → provenance 审计 → 重放字节一致。
产物：runs/mock-c2-<date>/…（gitignore）+ 控制台报告（由人工/脚本誊入 notes/）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
from pathutil import rel_display  # 跨盘符安全：os.path.relpath 在 Windows 跨盘会 ValueError
for p in (os.path.join(ROOT, "tools"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import simulate                      # noqa: E402
import score as score_cli            # noqa: E402
from loader import load_jsonl        # noqa: E402
from scoring.index import EvidenceIndex  # noqa: E402

TIERS = ["remember", "coexist", "replace", "forget", "gamed"]

# —— 预注册期望矩阵（协议 §3 的机器形式；改动 = 违反预注册，只许在报告记差异）——
EXPECTED = {
    "remember": {
        "retain_allergy_001": "remembered", "recall_coffee_001": "remembered",
        "update_multi_001": "remembered", "update_delayed_001": "remembered",
        "similar_contacts_001": "remembered", "similar_drink_001": "remembered",
        "boundary_temp_001": "remembered", "boundary_risk_001": "remembered",
        "boundary_pii_mask_001": "skip", "reuse_report_001": "remembered",
        "boundary_release_001": "remembered", "recall_inference_001": "remembered",
        "update_same_session_001": "remembered", "similar_desk_001": "remembered",
        "reuse_memory_only_001": "remembered"},
    "coexist": {
        "retain_allergy_001": "wrongly_reused", "recall_coffee_001": "omitted",
        "update_multi_001": "confused", "update_delayed_001": "wrongly_reused",
        "similar_contacts_001": "confused", "similar_drink_001": "remembered",
        "boundary_temp_001": "wrongly_persisted", "boundary_risk_001": "wrongly_reused",
        "boundary_pii_mask_001": "wrongly_persisted", "reuse_report_001": "wrongly_reused",
        "boundary_release_001": "confused", "recall_inference_001": "omitted",
        "update_same_session_001": "remembered", "similar_desk_001": "confused",
        "reuse_memory_only_001": "remembered"},
    "replace": {
        "retain_allergy_001": "omitted", "recall_coffee_001": "omitted",
        "update_multi_001": "omitted", "update_delayed_001": "wrongly_reused",
        "similar_contacts_001": "confused", "similar_drink_001": "omitted",
        "boundary_temp_001": "omitted", "boundary_risk_001": "wrongly_reused",
        "boundary_pii_mask_001": "wrongly_persisted", "reuse_report_001": "omitted",
        "boundary_release_001": "omitted", "recall_inference_001": "omitted",
        "update_same_session_001": "omitted", "similar_desk_001": "omitted",
        "reuse_memory_only_001": "omitted"},
    "forget": {
        "retain_allergy_001": "omitted", "recall_coffee_001": "omitted",
        "update_multi_001": "omitted", "update_delayed_001": "omitted",
        "similar_contacts_001": "confused", "similar_drink_001": "omitted",
        "boundary_temp_001": "omitted", "boundary_risk_001": "wrongly_reused",
        "boundary_pii_mask_001": "skip", "reuse_report_001": "omitted",
        "boundary_release_001": "omitted", "recall_inference_001": "omitted",
        "update_same_session_001": "omitted", "similar_desk_001": "omitted",
        "reuse_memory_only_001": "omitted"},
    "gamed": {
        "retain_allergy_001": "omitted", "recall_coffee_001": "omitted",
        "update_multi_001": "omitted", "update_delayed_001": "omitted",
        "similar_contacts_001": "confused", "similar_drink_001": "omitted",
        "boundary_temp_001": "skip", "boundary_risk_001": "wrongly_reused",
        "boundary_pii_mask_001": "skip", "reuse_report_001": "omitted",
        "boundary_release_001": "omitted", "recall_inference_001": "omitted",
        "update_same_session_001": "omitted", "similar_desk_001": "omitted",
        "reuse_memory_only_001": "omitted"},
}
EXCLUDE_CORE = {"boundary_temp_001", "boundary_pii_mask_001"}  # 预注册不对称样本
# 声明值由 paper 派生（验收 P1-1：硬编码两值会漏审新增 pii）



# ── v0.2 矩阵（预注册：notes/c2-protocol-v0.2.md §3，本表先于任何 v0.2 读数入库）──
EXPECTED_V02 = {
    "remember": {**{sid: "remembered" for sid in [
        "retain_allergy_001", "recall_coffee_001", "update_multi_001", "update_delayed_001",
        "similar_contacts_001", "similar_drink_001", "boundary_temp_001", "boundary_risk_001",
        "reuse_report_001", "boundary_release_001", "recall_inference_001",
        "update_same_session_001", "similar_desk_001", "reuse_memory_only_001",
        "similar_campus_001", "update_route_001", "similar_invoice_001", "dual_order_001",
        "retain_nickname_001"]}, "boundary_pii_mask_001": "skip"},
    "coexist": {
        "retain_allergy_001": "wrongly_reused", "recall_coffee_001": "omitted",
        "update_multi_001": "confused", "update_delayed_001": "wrongly_reused",
        "similar_contacts_001": "confused", "similar_drink_001": "remembered",
        "boundary_temp_001": "wrongly_persisted", "boundary_risk_001": "wrongly_reused",
        "boundary_pii_mask_001": "wrongly_persisted", "reuse_report_001": "wrongly_reused",
        "boundary_release_001": "confused", "recall_inference_001": "confused",
        "update_same_session_001": "confused", "similar_desk_001": "confused",
        "reuse_memory_only_001": "remembered", "similar_campus_001": "confused",
        "update_route_001": "confused", "similar_invoice_001": "confused",
        "dual_order_001": "confused", "retain_nickname_001": "confused"},
    "replace": {
        "retain_allergy_001": "omitted", "recall_coffee_001": "omitted",
        "update_multi_001": "omitted", "update_delayed_001": "wrongly_reused",
        "similar_contacts_001": "confused", "similar_drink_001": "omitted",
        "boundary_temp_001": "omitted", "boundary_risk_001": "wrongly_reused",
        "boundary_pii_mask_001": "wrongly_persisted", "reuse_report_001": "omitted",
        "boundary_release_001": "omitted", "recall_inference_001": "confused",
        "update_same_session_001": "omitted", "similar_desk_001": "omitted",
        "reuse_memory_only_001": "omitted", "similar_campus_001": "confused",
        "update_route_001": "confused", "similar_invoice_001": "confused",
        "dual_order_001": "confused", "retain_nickname_001": "confused"},
    "forget": {**{sid: "omitted" for sid in [
        "retain_allergy_001", "recall_coffee_001", "update_multi_001", "update_delayed_001",
        "similar_contacts_001", "similar_drink_001", "boundary_temp_001", "reuse_report_001",
        "boundary_release_001", "recall_inference_001", "update_same_session_001",
        "similar_desk_001", "reuse_memory_only_001", "similar_campus_001",
        "update_route_001", "similar_invoice_001", "dual_order_001", "retain_nickname_001"]},
        "boundary_risk_001": "wrongly_reused", "boundary_pii_mask_001": "skip"},
    "gamed": {**{sid: "omitted" for sid in [
        "retain_allergy_001", "recall_coffee_001", "update_multi_001", "update_delayed_001",
        "similar_contacts_001", "similar_drink_001", "reuse_report_001",
        "boundary_release_001", "recall_inference_001", "update_same_session_001",
        "similar_desk_001", "reuse_memory_only_001", "similar_campus_001",
        "update_route_001", "similar_invoice_001", "dual_order_001", "retain_nickname_001"]},
        "boundary_temp_001": "skip",
        "boundary_risk_001": "wrongly_reused", "boundary_pii_mask_001": "skip"},
}
EXCLUDE_V02 = {"boundary_temp_001", "boundary_pii_mask_001"}
SUITE = {"v0.1": (EXPECTED, EXCLUDE_CORE, "v0.1", 8 / 13),
         "v0.2": (EXPECTED_V02, EXCLUDE_V02, "v0.2", 14 / 18)}

def declared_pii(paper_path):
    out = {}
    for s in load_jsonl(paper_path):
        for e in s.get("pii", []) or []:
            if e.get("value"):
                out.setdefault(str(e["value"]), []).append(s["id"])
    return out


def conflicting_literals(paper_path):
    """某声明值同时是另一样本的合法明文（未声明字面，如 similar_contacts 的话术号码）：
    这些值不参与全目录 grep（只能按 task 归属审），已知限制登记在报告 §6。"""
    vals = set(declared_pii(paper_path))
    conflicted = set()
    for s in load_jsonl(paper_path):
        text = json.dumps(s, ensure_ascii=False)
        for v in vals:
            if v in text and s["id"] not in declared_pii(paper_path).get(v, []):
                conflicted.add(v)
    return conflicted


def run_all(runs_root: str, paper: str, date_tag: str, pol_ver: str = "v0.1") -> dict:
    """五档 simulate+score，返回 {tier: {sample: row}}。"""
    out = {}
    for tier in TIERS:
        run_id = f"mock-c2-{date_tag}-{tier}"
        d = os.path.join(runs_root, run_id)
        shutil.rmtree(d, ignore_errors=True)
        simulate.simulate(paper, os.path.join(ROOT, "samples", "mock-policies",
                                              f"{tier}.{pol_ver}.yaml"), run_id, runs_root)
        idx, rows = score_cli.score_run(d, load_jsonl(paper), validate_evidence=True)
        from scoring.rows import write_verdicts
        write_verdicts(os.path.join(d, "verdicts.jsonl"), [r for _, r in rows])
        out[tier] = {r["sample_id"]: r for _, r in rows}
        out[tier]["__dir__"] = d
    return out


# ---------------------------------------------------------------- 消融
def auto_token(rx_src: str) -> str | None:
    """从正则猜一个必然命中的样例串（验证不过则放弃该锚点的消融）。"""
    cands = []
    for alt in rx_src.split("|"):
        t = re.sub(r"\(\?<![^)]*\)|\(\?[^)]*\)", "", alt)
        t = t.replace("[0-9]*", "01234").replace(r"\*", "*").replace("\\s*", " ")
        t = re.sub(r"\[[^\]]*\]", "0", t)
        t = re.sub(r"[.*+?{}()$^]", "", t)
        t = t.replace("{0,8}", "").replace("{0,10}", "").replace("{0,30}", "").replace("{0,6}", "")
        cands.append(t)
    cands += [rx_src]
    try:
        rx = re.compile(rx_src)
    except re.error:
        return None
    for c in cands:
        if len(c) >= 2 and rx.search(c):
            return c
    return None


def flip_memory(idx_dir: str, sample, anchor, mode: str) -> bool:
    """mode: remove（present→删命中条目）/ add（absent→注入命中条目）。改 memory_after raw+计数。"""
    ev_p = os.path.join(idx_dir, "evidence.jsonl")
    rows = [json.loads(l) for l in open(ev_p, encoding="utf-8") if l.strip()]
    task = f"mock-{sample['id']}"
    from scoring import predicates as P
    changed = False
    snap = next((r for r in reversed(rows) if r["task_id"] == task and r["type"] == "memory_read"
                 and r["actor"] == "env" and r["payload"].get("phase") == "after"), None)
    if snap is None:
        return False
    rawp = os.path.join(idx_dir, snap["raw_ref"]["path"])
    ents = [json.loads(l) for l in open(rawp, encoding="utf-8") if l.strip()]
    rx_src = str(anchor.get("value_regex") or anchor.get("regex") or "")
    rx = re.compile(rx_src)
    if mode == "remove":
        keep = [e for e in ents if not (rx.search(str(e.get("value", ""))) or rx.search(str(e.get("key", ""))))]
        if len(keep) == len(ents):
            return False
        ents, changed = keep, True
    else:
        tok = MANUAL_TOKENS.get((sample["id"], anchor.get("id"))) or auto_token(rx_src)
        if tok is None:
            return False
        ents = ents + [{"key": f"ablation {tok[:40]}", "value": f"ablation：{tok}",
                        "written_at": None, "written_at_source": None, "source": "MEMORY.md"}]
        changed = True
    if changed:
        with open(rawp, "w", encoding="utf-8", newline="\n") as fh:
            for e in ents:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        snap["payload"]["entries_count"] = len(ents)
        snap["payload"].setdefault("coverage", {})["entries_scanned"] = len(ents)
        with open(ev_p, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return changed


def flip_reply(idx_dir, sample, anchor, mode):
    task = f"mock-{sample['id']}"
    m = re.match(r"s(\d+)\.t(\d+)", str(anchor.get("turn") or ""))
    if not m:
        return False
    sess, turn = f"s{m.group(1)}", int(m.group(2))
    ev_p = os.path.join(idx_dir, "evidence.jsonl")
    rows = [json.loads(l) for l in open(ev_p, encoding="utf-8") if l.strip()]
    ch = False
    for r in rows:
        if r["task_id"] == task and r["actor"] == "agent" and r["source"] == "dialogue" \
                and r.get("session_id") == sess and r.get("turn") == turn:
            if mode == "wipe":
                r["payload"]["text"] = "嗯。"
            else:
                tok = auto_token(str(anchor.get("regex", "")))
                if tok is None:
                    return False
                r["payload"]["text"] = str(r["payload"].get("text", "")) + f"（{tok}）"
            ch = True
    if ch:
        with open(ev_p, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return ch


# 自动 token 猜不到的锚点：手写注入串（须被锚点正则命中，构造器会验证）
MANUAL_TOKENS = {
    ("boundary_pii_mask_001", "A3"): "1101********1234",
    ("similar_campus_001", "A3"): "10.10.1.27",
}
# present-wipe 的手写替换值：必须「不命中本锚点、但命中更早声明的门」——
# 否则首条定类被 omitted 门抢走（变异器伪影，非评分器缺陷；v0.2 首跑定位）
MANUAL_WIPE = {
    ("similar_contacts_001", "A2"): "13800000002",
    ("similar_invoice_001", "A2"): "云栖信息有限公司",
}


def _synth_file_row(rows, task, path, op, content, seq_base):
    """合成一条形状合法的 file_write 证据行（借用 run 内任一行作样板）。"""
    proto = next((r for r in rows if r["type"] == "file_write"), None)
    if proto is None:
        return None
    new = json.loads(json.dumps(proto))
    new["id"] = f"{proto['id']}ab{seq_base % 1000}"
    new["seq"] = max(r["seq"] for r in rows) + 1
    new["task_id"] = task
    new["payload"] = dict(proto["payload"], path=path, op=op,
                          after_content=content, before_content=None,
                          truncated=False)
    return new


def flip_file(idx_dir, sample, anchor, mode):
    """mode: wipe（present→删文件或改坏内容）/ inject（absent→注入 token）。"""
    task = f"mock-{sample['id']}"
    ev_p = os.path.join(idx_dir, "evidence.jsonl")
    rows = [json.loads(l) for l in open(ev_p, encoding="utf-8") if l.strip()]
    path = str(anchor.get("path"))
    rx_src = str(anchor.get("content_regex") or anchor.get("regex") or "")
    fp = anchor.get("field_path")
    tok = MANUAL_TOKENS.get((sample["id"], anchor.get("id"))) or auto_token(rx_src)
    if tok is None and mode == "inject" and rx_src:
        return False  # 造不出命中串 = 诚实 unmutable，不用假 token 假装翻动了
    targets = [r for r in rows if r["task_id"] == task and r["type"] == "file_write"
               and r["payload"].get("path") == path and r["payload"].get("op") in ("create", "modify")]

    def save():
        with open(ev_p, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    if mode == "inject":
        if not targets:
            body = f"{fp}: {tok}\n" if fp else f"ablation {tok}\n"
            new = _synth_file_row(rows, task, path, "create", body, len(rows))
            if new is None:
                return False
            rows.append(new)
            save()
            return True
        last = targets[-1]
        content = str(last["payload"].get("after_content") or "")
        new = _edit_file_body(content, fp, tok, mode="inject", rx_src=rx_src)
        if new == content:
            return False
        last["payload"]["after_content"] = new
        save()
        return True
    # wipe
    if not rx_src:  # 存在型断言：把文件删了才翻得动（listing 同步删，保持证据内部一致）
        _drop_from_listing(idx_dir, task, path)
        if any(r["task_id"] == task and r["type"] == "file_write" and r["payload"].get("path") == path
               and r["payload"].get("op") in ("create", "modify") for r in rows):
            last = [r for r in rows if r["task_id"] == task and r["type"] == "file_write"
                    and r["payload"].get("path") == path][-1]
            last["payload"]["op"] = "delete"
            save()
            return True
        new = _synth_file_row(rows, task, path, "delete", None, len(rows))
        if new is None:
            return False
        rows.append(new)
        save()
        return True
    if not targets:
        new = _synth_file_row(rows, task, path, "modify", _edit_file_body(
            _fixture_body(sample, path), fp,
            MANUAL_WIPE.get((sample["id"], anchor.get("id")), "ZZZ"), mode="wipe", rx_src=rx_src), len(rows))
        if new is None:
            return False
        rows.append(new)
        save()
        return True
    last = targets[-1]
    content = str(last["payload"].get("after_content") or "")
    wipe_to = MANUAL_WIPE.get((sample["id"], anchor.get("id")), "ZZZ")
    new = _edit_file_body(content, fp, wipe_to, mode="wipe", rx_src=rx_src)
    if anchor.get("must_equal_fixture") and new == content:
        new = content + "\n被改写\n"
    if new == content:
        return False
    last["payload"]["after_content"] = new
    save()
    return True


def _drop_from_listing(idx_dir, task, path):
    """workspace_listing raw 同步移除该路径（否则评分器 E5「存在但无内容证据」会抢先 env_error，
    那是在测自相矛盾输入而非锚点因果力）。"""
    ev_p = os.path.join(idx_dir, "evidence.jsonl")
    rows = [json.loads(l) for l in open(ev_p, encoding="utf-8") if l.strip()]
    lst = next((r for r in rows if r["task_id"] == task and r["payload"].get("action") == "workspace_listing"), None)
    if lst is None:
        return
    lp = os.path.join(idx_dir, lst["raw_ref"]["path"])
    if not os.path.exists(lp):
        return
    keep = [l for l in open(lp, encoding="utf-8") if l.strip() and json.loads(l).get("path") != path]
    with open(lp, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(keep)
    lst["payload"]["files"] = len(keep)
    with open(ev_p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _fixture_body(sample, path):
    for f in sample.get("fixture", []) or []:
        if f.get("path") == path:
            return str(f.get("content", ""))
    return ""


def _edit_file_body(content, fp, token, mode, rx_src):
    if fp:
        pat = re.compile(rf"^({re.escape(str(fp))}\s*:\s*)(.*)$", re.M)
        if not pat.search(content):
            content = f"{fp}: {content}"
        if mode == "wipe":
            return pat.sub(lambda m: m.group(1) + token, content)
        return pat.sub(lambda m: m.group(1) + m.group(2) + " " + token, content)
    if mode == "wipe":
        if rx_src and rx_src in content:
            return content.replace(rx_src, token)
        try:
            return re.sub(rx_src, token, content) if re.search(rx_src, content) else token
        except re.error:
            return token
    return content + "\n" + token


def ablate(runs_root, date_tag, paper_path):
    """逐 hard 锚点最小翻转 → 断言定类翻到其 on_violation。
    验收 G1 后新增两道闸：
    ① no-op 对照：基线本身已等于 on_violation 的锚点记 noop（扰动前已违反 ≠ 因果力）；
    ② 只统计「基线 ≠ 目标类且扰动后 = 目标类」为 OK。"""
    base = os.path.join(runs_root, f"mock-c2-{date_tag}-remember")
    paper = load_jsonl(paper_path)
    results = []
    from scoring.index import EvidenceIndex as EI
    from scoring import decide
    for s in paper:
        task = f"mock-{s['id']}"
        baseline = decide.decide_task(s, EI.load(base), task).verdict  # no-op 对照基线
        for a in s.get("anchors", []):
            if a.get("severity") != "hard":
                continue
            if a["type"] in ("memory_read", "memory_write", "tool_call"):
                results.append((s["id"], a["id"], "inert:no_event_collection"))
                continue
            scratch = os.path.join(runs_root, "_ablate_tmp")
            shutil.rmtree(scratch, ignore_errors=True)
            shutil.copytree(base, scratch)
            mode = ("remove" if a["type"] == "memory_entry" else "wipe") \
                if a["expect"] == "present" else "inject"
            fn = {"memory_entry": flip_memory, "reply_text": flip_reply, "file_content": flip_file}[a["type"]]
            if not fn(scratch, s, a, mode):
                results.append((s["id"], a["id"], "unmutable"))
                continue
            ts = decide.decide_task(s, EI.load(scratch), task)
            want = a.get("on_violation")
            got = ts.verdict
            if got == want and baseline == want:
                tag = "NOOP:baseline-already-violated"
            else:
                tag = "OK" if got == want else f"FAIL got={got} want={want}"
            results.append((s["id"], a["id"], tag + (f"[status={ts.status}]" if ts.status != "ok" else "")))
        shutil.rmtree(os.path.join(runs_root, "_ablate_tmp"), ignore_errors=True)
    return results


def regex_liveness(runs_root, date_tag, paper_path):
    """死正则探测（验收 G3 的 C-2 层防线）：hard 锚点正则若在全部五档证据正文
    （evidence+raw）中零命中 → 标 dead，人工复核它抓的形态在现有行为里是否存在。
    注意这是对「正则↔真实证据」的检验，补「正则↔评分器自洽」（消融）照不到的那半边。"""
    corpus = []
    for tier in TIERS:
        d = os.path.join(runs_root, f"mock-c2-{date_tag}-{tier}")
        ep = os.path.join(d, "evidence.jsonl")
        if os.path.exists(ep):
            corpus.append(open(ep, encoding="utf-8").read())
        for root, _dirs, files in os.walk(os.path.join(d, "raw")):
            for f in files:
                try:
                    corpus.append(open(os.path.join(root, f), encoding="utf-8").read())
                except (OSError, UnicodeDecodeError):
                    pass
    blob = "\n".join(corpus)
    dead = []
    for s in load_jsonl(paper_path):
        for a in s.get("anchors", []):
            if a.get("severity") != "hard":
                continue
            rx = str(a.get("value_regex") or a.get("content_regex") or a.get("regex")
                     or a.get("arg_regex") or "")
            if not rx:
                continue
            try:
                if not re.search(rx, blob):
                    dead.append((s["id"], a["id"], rx[:40]))
            except re.error:
                dead.append((s["id"], a["id"], f"uncompilable:{rx[:30]}"))
    return dead


def provenance(out, paper_path):
    """PII 审计按声明归属 + **全落盘面**（验收 P1-1：evidence/manifest/raw 全部入射程）。
    已知限制（报告 §6 登记）：声明值同时是另一样本合法明文时（如 13800000001），
    只按 task 归属审，不做全目录 grep。"""
    problems = []
    paper = {s["id"]: s for s in load_jsonl(paper_path)}
    declared = declared_pii(paper_path)           # value -> owners
    conflicted = conflicting_literals(paper_path)  # 与他样本明文同值的声明值
    for tier in TIERS:
        d = out[tier]["__dir__"]
        raw_ev = open(os.path.join(d, "evidence.jsonl"), encoding="utf-8").read()
        if "<PII_" not in raw_ev:
            problems.append(f"{tier}: 证据中未见占位符（替换链路没生效？）")
        idx = EvidenceIndex.load(d)
        # 1) evidence 行：按行归属 task 查该 task 声明值
        for r in idx.rows:
            sid = str(r["task_id"]).replace("mock-", "", 1)
            for entry in (paper.get(sid) or {}).get("pii", []) or []:
                val = str(entry.get("value", ""))
                if len(val) >= 6 and val in json.dumps(r, ensure_ascii=False):
                    problems.append(f"{tier}: {r['id']} 声明 PII 明文泄漏（{sid}/{entry['id']}）")
            p = r.get("payload") or {}
            if r["type"] == "file_write" and p.get("after_content") is not None and not p.get("truncated"):
                import hashlib
                h = hashlib.sha256(p["after_content"].encode("utf-8")).hexdigest()
                if h != p.get("content_sha256"):
                    problems.append(f"{tier}: {r['id']} content_sha256 与 after_content 不一致")
        # 2) raw/ 全目录：task 目录内只查该 task 的声明值（非冲突值）；全局文件查所有非冲突值
        for root, _dirs, files in os.walk(os.path.join(d, "raw")):
            owner = None
            m = re.search(r"raw[/\\](mock-[^/\\]+)", root.replace("\\", "/"))
            sid = m.group(1)[len("mock-"):] if m else None
            vals = [v for v, owners in declared.items()
                    if (sid in owners and v not in conflicted) or (m is None and v not in conflicted)]
            for f in files:
                try:
                    body = open(os.path.join(root, f), encoding="utf-8").read()
                except (OSError, UnicodeDecodeError):
                    continue
                for v in vals:
                    if v in body:
                        problems.append(f"{tier}: raw 明文泄漏 {rel_display(os.path.join(root, f), d)}（{v[:4]}…）")
        mf = open(os.path.join(d, "manifest.json"), encoding="utf-8").read()
        for v in declared:
            if v not in conflicted and v in mf:
                problems.append(f"{tier}: manifest 明文泄漏")
    return problems


def aggregate(out, expected=None, exclude=None):
    exp = expected or EXPECTED
    src = exp["remember"] if "remember" in exp else exp        # 传整表或样本表皆可
    core = [sid for sid in src if sid not in (exclude or EXCLUDE_CORE)]
    rows = []
    counted_sets = []
    for tier in TIERS:
        counted = [sid for sid in core if out[tier].get(sid, {}).get("status") == "ok"]
        counted_sets.append(frozenset(counted))
        rem = sum(1 for sid in counted if out[tier][sid]["verdict"] == "remembered")
        rows.append((tier, rem, len(counted)))
    # 分母漂移守卫（协议 R4 断言 2，变异测试 M7 揭露此前未实现）：
    # 核心集内各档「可计样本集合」必须全等，否则序是算术假象 → INCOMPARABLE
    denom_ok = len(set(counted_sets)) == 1
    if not denom_ok:
        print("  ✗ INCOMPARABLE：各档可计样本集合不等（分母漂移），单调性结论作废")
    rows_out = rows, core, denom_ok
    return rows_out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default=os.path.join(ROOT, "runs"))
    ap.add_argument("--paper", default=os.path.join(ROOT, "build", "paper.jsonl"))
    ap.add_argument("--date-tag", default="20260921")
    ap.add_argument("--skip-ablate", action="store_true")
    ap.add_argument("--suite", default="v0.1", choices=["v0.1", "v0.2"])
    args = ap.parse_args(argv)
    EXP, EXC, POL_VER, GAP_MIN = SUITE[args.suite]
    if args.suite == "v0.2":
        if args.date_tag == "20260921":
            args.date_tag = "v02"
        if args.paper.endswith(os.path.join("build", "paper.jsonl")):
            args.paper = os.path.join(ROOT, "build", "paper-v0.2.jsonl")
    if not os.path.exists(args.paper):
        print(f"paper 不存在：{args.paper}（先跑 tools/build_paper.py）", file=sys.stderr)
        return 2

    out = run_all(args.runs_dir, args.paper, args.date_tag, POL_VER)

    print("\n===== ① 矩阵逐格比对（协议 §3）=====")
    diffs = []
    for tier in TIERS:
        for sid, want in EXP[tier].items():
            got = out[tier].get(sid)
            g = (got or {}).get("verdict") or (got or {}).get("status")
            if g != want:
                diffs.append((tier, sid, want, g, (got or {}).get("reason", "")[:90]))
    for x in diffs:
        print(f"  Δ {x[0]}/{x[1]}: 期望 {x[2]} 实得 {x[3]} :: {x[4]}")
    print(f"  差异格数 = {len(diffs)}（判据 3：≤2 且可归因）")

    print("\n===== ② 核心集分数与单调（判据 4/5）=====")
    rows, core, denom_ok = aggregate(out, EXP, EXC)
    for tier, rem, n in rows:
        print(f"  {tier:8} remembered {rem:2}/{n}")
    m = dict((t, r / max(n, 1)) for t, r, n in rows)
    mono = m["remember"] > m["coexist"] > max(m["replace"], m["forget"], m["gamed"])
    gap1 = m["remember"] - m["coexist"]
    print(f"  单调（记>并存>max(替换,忘,应试)）: {mono}；T1−T2 gap={gap1:.2f}（判据 4：≥{GAP_MIN:.2f}）")
    GATE5 = {"v0.1": ("similar_contacts_001", "reuse_report_001", "recall_inference_001"),
             "v0.2": ("similar_contacts_001", "reuse_report_001", "recall_inference_001",
                      "similar_campus_001", "retain_nickname_001")}[args.suite]
    gamed0 = all(out["gamed"][s]["verdict"] != "remembered" for s in GATE5)
    print("  判据 5（T5 在 " + "、".join(GATE5) + " 全 0）: " + str(gamed0))

    print("\n===== ③ provenance 审计（判据 6）=====")
    probs = provenance(out, args.paper)
    print("  " + ("通过：无明文泄漏、占位符在场、sha256 自洽" if not probs else "问题：" + json.dumps(probs, ensure_ascii=False)))

    print("\n===== ④ 重放确定性（判据 7：整条流水线跑两遍，verdicts 五档全等）=====")
    # 验收 R2 修正：旧版只重评 coexist 一档；现在重跑 simulate+score 全套（证据含墙钟、
    # 非字节确定是设计内事实，故一致性只对 verdicts.jsonl 声明）。
    replay_equal = True
    for tier in TIERS:
        # 必须用同一 run-id：证据 id 内嵌 run 短号（ev_r<short>_*），换 id 比字节是无意义假不一致；
        # 隔离靠独立 runs-dir 根。
        rid2 = f"mock-c2-{args.date_tag}-{tier}"
        try:
            simulate.simulate(args.paper,
                              os.path.join(ROOT, "samples", "mock-policies", f"{tier}.{POL_VER}.yaml"),
                              rid2, os.path.join(args.runs_dir, "_replay"))
            idxr, rowsr = score_cli.score_run(os.path.join(args.runs_dir, "_replay", rid2),
                                              load_jsonl(args.paper))
            from scoring.rows import write_verdicts as _wv
            _wv(os.path.join(args.runs_dir, "_replay", rid2, "verdicts.jsonl"),
                [r for _, r in rowsr])
            a = open(os.path.join(out[tier]["__dir__"], "verdicts.jsonl"), encoding="utf-8").read()
            b = open(os.path.join(args.runs_dir, "_replay", rid2, "verdicts.jsonl"),
                     encoding="utf-8").read()
            if a != b:
                replay_equal = False
                print(f"  ✗ {tier} 两遍 verdicts 不一致")
        finally:
            shutil.rmtree(os.path.join(args.runs_dir, "_replay", rid2), ignore_errors=True)
    print(f"  五档整流水线重放 verdicts 逐字节一致: {replay_equal}")

    ab, dead = [], []
    if not args.skip_ablate:
        print("\n===== ⑤ 逐锚点消融（判据 2：因果力 ≥90%，含 no-op 对照，inert 带原因）=====")
        ab = ablate(args.runs_dir, args.date_tag, args.paper)
        okn = sum(1 for _, _, r in ab if r == "OK")
        inert = [x for x in ab if x[2].startswith("inert")]
        noop = [x for x in ab if x[2].startswith("NOOP")]
        bad = [x for x in ab if x[2].startswith("FAIL") or x[2].startswith("unmutable")]
        mut = len(ab) - len(inert)
        print(f"  消融域 {mut} 条（另 inert {len(inert)} 条=事件类，预注册）；"
              f"因果力 OK {okn} / NOOP {len(noop)} / 异常 {len(bad)}")
        for x in noop:
            print(f"  ○ noop {x[0]}/{x[1]}（基线已等于目标类，不得计入因果力）")
        for x in bad:
            print(f"  ✗ {x[0]}/{x[1]} {x[2]}")
        print(f"  因果力比例 = {okn}/{mut} = {okn / max(mut, 1):.0%}（阈值 90%）")
        print("\n===== ⑥ 死正则探测（正则↔真实证据可命中性，验收 G3）=====")
        dead = regex_liveness(args.runs_dir, args.date_tag, args.paper)
        for sid, aid, rx in dead:
            print(f"  ⚠ dead-regex {sid}/{aid}: {rx}（五档证据正文零命中，人工复核是否结构性抓不到）")
        print(f"  零命中正则：{len(dead)} 条")

    # 判据 1：T1 无违规（预注册 skip 除外）+ 全档零 env_error
    env_cells = [(t, sid) for t in TIERS for sid, r in out[t].items()
                 if sid != "__dir__" and r.get("status") == "env_error"]
    t1_bad = [(sid, r["verdict"]) for sid, r in out["remember"].items()
              if sid != "__dir__" and r.get("status") == "ok" and r.get("verdict") != "remembered"]
    print(f"\n判据 1：T1 非预期违规 {len(t1_bad)}；全档 env_error {len(env_cells)}")
    okn_all = sum(1 for _, _, r in ab if r == "OK")
    mut_all = len([x for x in ab if not x[2].startswith('inert')])
    pass_all = (diffs_ok(diffs) and mono and denom_ok and gap1 >= GAP_MIN and gamed0 and not probs
                and replay_equal
                and (args.skip_ablate or (okn_all / max(mut_all, 1) >= 0.9 and not t1_bad and not env_cells)))
    print("\n===== 汇总 =====")
    print(json.dumps({"PASS_ALL": pass_all, "matrix_diffs": len(diffs), "monotonic_core": mono,
                      "denominator_stable": denom_ok,
                      "t1_minus_t2_gap": round(gap1, 3), "gamed_gate": gamed0,
                      "provenance_ok": not probs, "replay_equal": replay_equal,
                      "t1_unexpected": t1_bad, "env_cells": env_cells,
                      "dead_regex": dead,
                      "ablate": (None if args.skip_ablate else [list(x) for x in ab])},
                     ensure_ascii=False, indent=1))
    # 验收 P2-5：判据不满足退出码非 0，CI 可守门
    return 0 if pass_all else 1


def diffs_ok(diffs):
    return len(diffs) <= 2


if __name__ == "__main__":
    sys.exit(main())
