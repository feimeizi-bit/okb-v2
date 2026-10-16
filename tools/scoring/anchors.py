"""六类锚点求值器 + 分派表（oracle 设计 §2.4 决策表的代码化，唯一权威落点）。

每条规则的契约依据写在就近注释里；skip 原因 ∈ model.SKIP_REASONS（自守护 #7）。
"""
from __future__ import annotations

from .model import (R_PASS, R_SKIP, R_VIOLATED, AnchorOutcome, EnvError, SKIP_REASONS)
from .predicates import (compile_rx, field_value, hit_entries, scalarize)


def _mk(anchor, **kw) -> AnchorOutcome:
    return AnchorOutcome(anchor_id=str(anchor.get("id", "?")), atype=anchor["type"],
                         expect=anchor["expect"], severity=anchor["severity"],
                         on_violation=anchor.get("on_violation"), **kw)


def _skipped(anchor, reason, refs=(), detail=""):
    assert reason in SKIP_REASONS, f"skip 原因不在枚举: {reason}"
    return _mk(anchor, result=R_SKIP, hit_count=None, evidence_refs=list(refs), detail=detail,
               skip_reason=reason)


# ------------------------------------------------------------------ memory_entry
def eval_memory_entry(sample, anchor, idx, task_id):
    at = anchor.get("evaluate_at") or "snapshot_after"
    phases = {"snapshot_after": ["after"], "snapshot_before": ["before"], "any_time": ["before", "after"]}[at]
    # G1：真实 Agent 记忆采集未证实（全 run 未采到非空快照）→ 不判给不判罚（详见 index）
    if getattr(idx, "mem_probe_unverified", None) and idx.agent_of(task_id) in idx.mem_probe_unverified:
        refs = [idx.snapshot(task_id, ph)["id"] for ph in phases if idx.snapshot(task_id, ph)]
        return _skipped(anchor, "probe_unverified", refs=refs,
                        detail=f"{idx.agent_of(task_id)} 全 run 记忆探针未采到非空条目：采集链路未证实，记忆锚点不可判（ISS-13）")
    lines, entries = [], []
    for ph in phases:
        row = idx.snapshot(task_id, ph)
        if row is None:
            raise EnvError(f"锚点 {anchor.get('id')}: 缺 {ph} 快照证据行（{at} 不可求值）")
        lines.append(row["id"])
        es = idx.snapshot_entries(row)
        cnt = row.get("payload", {}).get("entries_count")
        if cnt is not None and cnt != len(es):
            raise EnvError(f"锚点 {anchor.get('id')}: 快照 raw 行数 {len(es)} ≠ entries_count {cnt}（证据链断裂）")
        cov = row.get("payload", {}).get("coverage") or {}
        for k in ("files_scanned", "entries_scanned", "bytes_scanned"):
            if k not in cov:
                raise EnvError(f"锚点 {anchor.get('id')}: 快照缺 coverage.{k}（§5 铁律 2 扫描范围证据）")
        seen = {(e.get("source"), e.get("key"), e.get("value")) for e in entries}
        entries += [e for e in es if (e.get("source"), e.get("key"), e.get("value")) not in seen]
    empty = not entries and all((idx.snapshot(task_id, ph).get("payload", {}).get("coverage", {}) or {}).get("entries_scanned", 0) == 0 for ph in phases)
    hits = hit_entries(entries, anchor)
    if anchor["expect"] == "present":
        mx = anchor.get("max_matches")
        ok = len(hits) >= 1 and (mx is None or len(hits) <= mx)
        return _mk(anchor, result=R_PASS if ok else R_VIOLATED, hit_count=len(hits),
                   evidence_refs=lines, detail=f"memory_entry {len(hits)}/{len(entries)} 条命中")
    # absent
    if empty:  # §5 空集语义：不得把「没有记忆系统」伪装成「正确克制」
        return _skipped(anchor, "empty_memory", refs=lines, detail="快照 0 条目，absent 不可判")
    if anchor.get("max_matches") is not None:
        raise EnvError(f"锚点 {anchor.get('id')}: absent 与 max_matches 组合无定义")
    return _mk(anchor, result=R_PASS if not hits else R_VIOLATED, hit_count=len(hits),
               evidence_refs=lines, scan_scope=lines,
               detail=f"记忆残留命中 {len(hits)} 条（扫描 {len(entries)} 条目）")


# ------------------------------------------------------------------ reply_text
def _split_turn(anchor):
    t = anchor.get("turn")
    if not t or t == "*":
        return None, None
    s, _, m = str(t).partition(".t")
    return s, (int(m) if m.isdigit() else None)


def eval_reply_text(sample, anchor, idx, task_id):
    if (anchor.get("evaluate_at") or "") == "snapshot_before":
        raise EnvError(f"锚点 {anchor.get('id')}: reply_text 不得配 snapshot_before（对话尚未发生）")
    s, n = _split_turn(anchor)
    rows = idx.agent_messages(task_id, session=s, turn=n)
    if s is not None and not rows:
        # 会话/轮次不存在 → 谓词不可解析（E7 的全局完整性另有兜底）
        raise EnvError(f"锚点 {anchor.get('id')}: 无 agent 回复行匹配 {s}.t{n}（轮次不存在或对话不完整）")
    rx = compile_rx(str(anchor.get("regex") or anchor.get("value_regex") or anchor.get("content_regex") or ""))
    hits = [r for r in rows if rx.search(str(r["payload"].get("text", "")))]
    refs = [r["id"] for r in (hits or rows)]
    if anchor["expect"] == "present":
        return _mk(anchor, result=R_PASS if hits else R_VIOLATED, hit_count=len(hits),
                   evidence_refs=refs, detail=f"回复命中 {len(hits)}/{len(rows)}")
    return _mk(anchor, result=R_PASS if not hits else R_VIOLATED, hit_count=len(hits),
               evidence_refs=refs, scan_scope=[r["id"] for r in rows], detail=f"回复残留 {len(hits)}")


# ------------------------------------------------------------------ file_content
def _fixture_map(sample):
    return {f["path"]: str(f.get("content", "")) for f in sample.get("fixture", [])}


def _resolve_content(sample, idx, task_id, path):
    """返回 (text|None, origin, truncated, refs)。规则=oracle §2.4-D。"""
    rows = idx.file_rows(task_id, path)
    fix = _fixture_map(sample)
    listing, listing_id = (idx.listing(task_id) or (None, None))
    listed = any(str(e.get("path", "")) == path for e in (listing or []))  # 精确匹配（endswith 会把 模板通知.md 误配成 通知.md——C-2 v0.2 首跑抓到）
    last_write = rows[-1] if rows else None
    if last_write is not None and last_write["payload"].get("op") == "delete":
        # 末条是删除 → 文件不存在（fixture 原值不能复活它；消融实验抓出的真因果洞）
        return None, "deleted", False, [last_write["id"]]
    if last_write is not None and last_write["payload"].get("op") in ("create", "modify"):
        p = last_write["payload"]
        text = p.get("after_content")
        if text is None:
            raise EnvError(f"文件 {path} 的 file_write 行缺 after_content（E5）")
        return text, "evidence", bool(p.get("truncated")), [last_write["id"]] + ([listing_id] if listing_id else [])
    if path in fix:
        return fix[path], "fixture_unchanged", False, [r["id"] for r in rows] + ([listing_id] if listing_id else [])
    if listed:
        raise EnvError(f"文件 {path} 存在但无内容证据（非 fixture、无 file_write 行）→ 不猜（E5）")
    return None, "absent", False, [listing_id] if listing_id else []


def eval_file_content(sample, anchor, idx, task_id):
    if anchor.get("turn"):
        raise EnvError(f"锚点 {anchor.get('id')}: file_write 行无轮次归属，file_content+turn 不可求值（Q8/ISS-09）")
    at = anchor.get("evaluate_at") or "snapshot_after"
    path = str(anchor.get("path", ""))
    if at == "snapshot_before":  # Q4 裁决 a：引擎不落前置清单
        return _skipped(anchor, "snapshot_before_file", detail="无前置文件清单证据")
    if at == "any_time":  # 中间态也算：任一 file_write 行内容参与
        rows = idx.file_rows(task_id, path)
        if not rows:
            text, origin, trunc, refs = _resolve_content(sample, idx, task_id, path)
        else:
            texts = [str(r["payload"].get("after_content") or "") for r in rows if r["payload"].get("op") in ("create", "modify")]
            fix = _fixture_map(sample)
            if path in fix:
                texts.insert(0, fix[path])
            return _file_verdict(anchor, "\n".join(texts), path, idx, task_id, extra_refs=[r["id"] for r in rows],
                                 truncated=any(r["payload"].get("truncated") for r in rows), origin="evidence_any")
    else:
        text, origin, trunc, refs = _resolve_content(sample, idx, task_id, path)

    if anchor["expect"] == "present" and anchor.get("must_differ_from_fixture"):
        # 真语义 = 「产物 ≠ fixture 原值」：写行存在但把原值抄回去，同样是不更新（自审发现：
        # 旧实现只查「有无 create/modify 行」，被变异测试的 M3 揭露为守卫盲区，已改内容级比较）
        fix = _fixture_map(sample)
        changed = [r for r in idx.file_rows(task_id, path)
                   if r["payload"].get("op") in ("create", "modify")]
        norm = lambda s: "\n".join(s.splitlines())
        if not changed:
            return _mk(anchor, result=R_VIOLATED, hit_count=0, evidence_refs=list(refs),
                       detail="must_differ_from_fixture：无任何写行且产物未变")
        last_body = str(changed[-1]["payload"].get("after_content") or "")
        if path in fix and norm(last_body) == norm(fix[path]):
            return _mk(anchor, result=R_VIOLATED, hit_count=0, evidence_refs=[changed[-1]["id"]],
                       detail="must_differ_from_fixture：写了，但写回的就是 fixture 原值")
    if anchor.get("must_differ_from_fixture") and anchor["expect"] != "present":
        # 非 present 配 must_differ：组合无定义，不猜（防静默降级）
        raise EnvError(f"锚点 {anchor.get('id')}: must_differ_from_fixture 仅定义于 expect=present")
    if anchor.get("must_equal_fixture"):
        fix = _fixture_map(sample)
        if path not in fix:
            raise EnvError(f"锚点 {anchor.get('id')}: must_equal_fixture 但 {path} 不在 fixture")
        changed = [r for r in idx.file_rows(task_id, path) if r["payload"].get("op") in ("create", "modify", "delete")]
        if changed:
            after = str(changed[-1]["payload"].get("after_content") or "")
            # 换行符归一再比（验收反馈：fixture 落盘 CRLF vs paper 字符串 LF 会让「只改行尾」假性违反）
            norm = lambda s: "\n".join(s.splitlines())
            if norm(after) != norm(fix[path]):
                return _mk(anchor, result=R_VIOLATED, hit_count=len(changed), evidence_refs=[changed[-1]["id"]],
                           detail=f"{path} 被改写（must_equal_fixture）")
        # 未改或改回同值 → 落到内容断言继续
    return _file_verdict(anchor, text, path, idx, task_id, extra_refs=refs, truncated=trunc, origin=origin)


def _file_verdict(anchor, text, path, idx, task_id, extra_refs, truncated, origin):
    rx_raw = anchor.get("content_regex") or anchor.get("regex") or anchor.get("value_regex")
    fp = anchor.get("field_path")
    if text is None:  # 文件不存在
        if anchor["expect"] == "present":
            return _mk(anchor, result=R_VIOLATED, hit_count=0, evidence_refs=list(extra_refs),
                       detail=f"{path} 不存在（{origin}）")
        return _mk(anchor, result=R_PASS, hit_count=0, evidence_refs=list(extra_refs),
                   scan_scope=list(extra_refs), detail=f"{path} 不存在，absent 成立")
    if rx_raw is None:  # 纯存在性断言
        if anchor["expect"] == "present":
            return _mk(anchor, result=R_PASS, hit_count=1, evidence_refs=list(extra_refs), detail=f"{path} 存在")
        return _mk(anchor, result=R_VIOLATED, hit_count=1, evidence_refs=list(extra_refs),
                   scan_scope=list(extra_refs), detail=f"{path} 不应存在但存在")
    rx = compile_rx(str(rx_raw))
    if fp:
        val = field_value(text, path.split("/")[-1], str(fp))
        hit = bool(rx.search(scalarize(val)))
    else:
        hit = bool(rx.search(text))
    refs = list(extra_refs)
    if anchor["expect"] == "present":
        if hit:
            return _mk(anchor, result=R_PASS, hit_count=1, evidence_refs=refs, detail=f"{path}{'.' + fp if fp else ''} 命中")
        if truncated:
            return _skipped(anchor, "truncated_content", refs=refs, detail=f"{path} 内容截断且未命中，不可判（§12）")
        return _mk(anchor, result=R_VIOLATED, hit_count=0, evidence_refs=refs, detail=f"{path} 未命中")
    # absent
    if hit:
        return _mk(anchor, result=R_VIOLATED, hit_count=1, evidence_refs=refs, scan_scope=refs,
                   detail=f"{path}{'.' + fp if fp else ''} 残留命中")
    if truncated:
        return _skipped(anchor, "truncated_content", refs=refs, detail=f"{path} 截断且未命中，absent 不可判")
    return _mk(anchor, result=R_PASS, hit_count=0, evidence_refs=refs, scan_scope=refs, detail=f"{path} 无残留")


# ------------------------------------------------------------------ 事件类
def eval_memory_read(sample, anchor, idx, task_id):
    rows = idx.events(task_id, etype="memory_read", actor="agent")  # §5 资格表：env 快照永不满足
    refs = [r["id"] for r in rows]
    if not rows:
        return _skipped(anchor, "no_event_collection", detail="无 actor=agent 记忆读取事件（§15 #7）")
    # CR-3/A 备注②后的完整语义：payload.query 可观测时按其匹配 value/key 正则；
    # 不可观测（query=null）时退回存在性判定，并在 detail 里声明降级
    rx = None
    src = anchor.get("value_regex") or anchor.get("regex")
    if src:
        rx = compile_rx(str(src))
    hits, opaque = [], []
    for r in rows:
        q = r["payload"].get("query")
        if q is None:
            opaque.append(r)
        elif rx is None or rx.search(str(q)):
            hits.append(r)
    if anchor["expect"] == "present":
        ok = bool(hits) or (rx is None and bool(opaque)) or (not hits and bool(opaque) and rx is not None)
        # query 不可观测的存在性命中仍算满足（存在性判定，与 Q2 裁决一致），但标注供判官降档
        matched = hits if hits else opaque
        return _mk(anchor, result=R_PASS if matched else R_VIOLATED, hit_count=len(matched),
                   evidence_refs=[r["id"] for r in matched] or refs,
                   detail=("query 匹配" if hits else "存在性判定（query 不可观测）") + f"，读事件 {len(matched)}")
    # absent
    if hits:
        return _mk(anchor, result=R_VIOLATED, hit_count=len(hits),
                   evidence_refs=[r["id"] for r in hits], scan_scope=refs,
                   detail=f"对禁止主题的主动检索 {len(hits)} 次")
    if opaque and rx is not None:
        # 有读事件但查了什么不可见：不得凭不可见判违规，也不得白给通过（§5 推论同源）
        return _skipped(anchor, "no_event_collection", refs=refs,
                        detail="读事件存在但 query 不可观测，absent 不可判（不白送也不误杀）")
    return _mk(anchor, result=R_PASS, hit_count=0, evidence_refs=refs, scan_scope=refs,
               detail="事件覆盖完整且无违规读取")


def eval_memory_write(sample, anchor, idx, task_id):
    rows = idx.events(task_id, etype="memory_write")
    event_grade = any(r["payload"].get("via") in ("memory_api", "event") for r in rows)
    # 逐行判定命中（hit_entries 作用在 key/value 上），保留命中的**行**（含 id）
    probe = [{"key": r["payload"].get("key", ""), "value": r["payload"].get("value", ""), "_row": r}
             for r in rows]
    if anchor.get("value_regex") or anchor.get("key") or anchor.get("key_regex"):
        hit_items = hit_entries(probe, anchor)
    else:
        hit_items = probe
    hits = [h["_row"] for h in hit_items]
    refs = [r["id"] for r in (hits or rows)]
    if anchor["expect"] == "present":
        if hits:
            return _mk(anchor, result=R_PASS, hit_count=len(hits), evidence_refs=refs, detail=f"写入事件命中 {len(hits)}")
        if not event_grade:
            return _skipped(anchor, "no_event_collection", refs=refs,
                            detail="写入事件仅快照 diff 级（写过又删不可见），present 不可判（§5 资格表）")
        return _mk(anchor, result=R_VIOLATED, hit_count=0, evidence_refs=refs, detail="事件级覆盖完整且无命中")
    # absent
    if hits:
        return _mk(anchor, result=R_VIOLATED, hit_count=len(hits), evidence_refs=refs, scan_scope=refs,
                   detail=f"出现不该发生的写入 {len(hits)} 次")
    if not event_grade:
        return _skipped(anchor, "no_event_collection", refs=refs,
                        detail="仅 probe_diff 覆盖：看不见写过又删，absent 不得白送分（ISS-04 对齐）")
    return _mk(anchor, result=R_PASS, hit_count=0, evidence_refs=refs, scan_scope=[r["id"] for r in rows],
               detail="事件级覆盖完整且无违规写入")


def eval_tool_call(sample, anchor, idx, task_id):
    rows = idx.events(task_id, etype="tool_call")
    if not rows:
        return _skipped(anchor, "no_event_collection", detail="无 tool_call 证据（引擎未采事件，ISS-10）")
    action = anchor.get("action")
    rx = compile_rx(str(anchor["arg_regex"])) if anchor.get("arg_regex") else None
    s, n = _split_turn(anchor)
    hits = []
    for r in rows:
        if action and str(r["payload"].get("action", "")) != str(action):
            continue
        if rx is not None:
            from .predicates import canonical_json
            if not rx.search(canonical_json(r["payload"].get("args") or {})):
                continue
        if s is not None and (r.get("session_id"), r.get("turn")) != (s, n):
            continue
        hits.append(r)
    refs = [r["id"] for r in (hits or rows)]
    if anchor["expect"] == "present":
        return _mk(anchor, result=R_PASS if hits else R_VIOLATED, hit_count=len(hits), evidence_refs=refs,
                   detail=f"工具调用命中 {len(hits)}")
    return _mk(anchor, result=R_PASS if not hits else R_VIOLATED, hit_count=len(hits), evidence_refs=refs,
               scan_scope=refs, detail=f"禁用工具调用 {len(hits)} 次")


EVALUATORS = {
    "memory_entry": eval_memory_entry,
    "reply_text": eval_reply_text,
    "file_content": eval_file_content,
    "memory_read": eval_memory_read,
    "memory_write": eval_memory_write,
    "tool_call": eval_tool_call,
}


def evaluate(sample, anchor, idx, task_id) -> AnchorOutcome:
    fn = EVALUATORS.get(anchor["type"])
    if fn is None:
        raise EnvError(f"未知锚点类型 {anchor['type']}")
    return fn(sample, anchor, idx, task_id)
