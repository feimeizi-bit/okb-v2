#!/usr/bin/env python3
"""规则评分器关键单测（无 pytest：`python tools/tests/test_score_units.py`，全过 exit 0）。

覆盖 oracle T1/T3/T5/T11/T12/T13 + metis guard#5 与 R11 消融的最小等价物。
构造假 evidence（走 EvidenceIndex，不经 runner），断言「不报错但给错分」的路径被堵住。
"""
from __future__ import annotations
from paper_fixture import ensure_pilot_jsonl  # build/ 不在 VM 同步范围：缺了就重建并核对冻结 sha

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(ROOT, "tools"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from scoring import decide
from scoring.index import EvidenceIndex
from scoring.model import R_PASS, R_SKIP, R_VIOLATED, AnchorOutcome
from scoring.rows import check_row, verdict_row

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def mkrun(tmp, rows, manifest=None):
    d = os.path.join(tmp, "run")
    os.makedirs(os.path.join(d, "raw", "t1"), exist_ok=True)
    with open(os.path.join(d, "evidence.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    man = manifest or {"run_id": "r001", "agents": [{"name": "mock"}], "contract_version": "v0.1"}
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False)
    return d


def ev(i, ttype, actor, payload, task="t1", **kw):
    r = {"id": f"ev_{i:03d}", "run_id": "r001", "seq": i, "ts": "2026-09-21T00:00:00+08:00",
         "task_id": task, "session_id": None, "turn": None, "source": kw.get("source", "memory"),
         "type": ttype, "actor": actor, "payload": payload,
         "raw_ref": kw.get("raw_ref", {"path": f"raw/{task}/x.jsonl", "line": 1}), "pii_hits": []}
    return r


def sample(**over):
    s = {"id": "sX", "dimension": "dynamic_update", "score_policy": "hybrid",
         "script": [{"session": "s1", "phase": "probe", "turns": [{"text": "a"}]}],
         "fixture": [], "anchors": [], "expect": {"verdict": "remembered"}}
    s.update(over)
    return s


def summary_ok(i):
    return ev(i, "message", "env", {"action": "task_summary", "task_id": "t1", "status": "ok",
                                    "reason": None, "duration_ms": 1, "counts": {}}, source="system")


def one_agent_reply(i, text="好的", session="s1", turn=1):
    r = ev(i, "message", "agent", {"text": text}, source="dialogue")
    r["session_id"], r["turn"] = session, turn
    return r


def main() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()

    # ---- U1 空快照：present 可判（VIOLATED），absent 不可判（SKIP），不得白送分
    rows = [summary_ok(1), one_agent_reply(4, "好的"),
            ev(2, "memory_read", "env", {"phase": "after", "entries_count": 0,
                                         "coverage": {"files_scanned": 1, "entries_scanned": 0,
                                                      "bytes_scanned": 0, "sources": ["MEMORY.md"]}}),
            ev(3, "memory_read", "env", {"phase": "before", "entries_count": 0,
                                         "coverage": {"files_scanned": 1, "entries_scanned": 0,
                                                      "bytes_scanned": 0, "sources": ["MEMORY.md"]}})]
    run1 = mkrun(tmp, rows)
    rawp = os.path.join(run1, "raw", "t1", "x.jsonl")
    open(rawp, "w").close()
    idx = EvidenceIndex.load(run1)
    s = sample(anchors=[
        {"id": "A1", "type": "memory_entry", "key": "*", "value_regex": "花生",
         "expect": "present", "severity": "hard", "on_violation": "omitted"},
        {"id": "A2", "type": "memory_entry", "key": "*", "value_regex": "老地址",
         "expect": "absent", "severity": "soft"}])
    ts = decide.decide_task(s, idx, "t1")
    from scoring import anchors as A
    o1 = A.evaluate(s, s["anchors"][0], idx, "t1")
    o2 = A.evaluate(s, s["anchors"][1], idx, "t1")
    check("U1a present@空快照 = VIOLATED", o1.result == R_VIOLATED)
    check("U1b absent@空快照 = SKIP(empty_memory)", o2.result == R_SKIP and o2.skip_reason == "empty_memory")
    check("U1c 整行不得给 remembered", ts.verdict != "remembered")

    # ---- U2 guard#5：唯一 hard 通过但有 hard absent 被 skip → 整行降 skip（R3 洞）
    snap = os.path.join(run1, "raw", "t1", "snap_after.jsonl")
    open(snap, "w").close()
    rows2 = [summary_ok(1), one_agent_reply(3, "好的"),
             ev(2, "memory_read", "env", {"phase": "after", "entries_count": 0,
                                          "coverage": {"files_scanned": 1, "entries_scanned": 0,
                                                       "bytes_scanned": 0, "sources": ["MEMORY.md"]},
                                          }, raw_ref={"path": "raw/t1/snap_after.jsonl", "line": 1})]
    idx2 = EvidenceIndex.load(mkrun(tmp, rows2))
    s2 = sample(anchors=[
        {"id": "A1", "type": "memory_entry", "key": "*", "value_regex": "x",
         "expect": "absent", "severity": "hard", "on_violation": "confused"}])
    ts2 = decide.decide_task(s2, idx2, "t1")
    check("U2 hard 全 skip → status=skip 且 verdict=None", ts2.status == "skip" and ts2.verdict is None)

    # ---- U3 快照行（actor=env）不得满足 memory_read 事件锚点（ISS-03）
    rows3 = [summary_ok(1), one_agent_reply(2, "好的"),
             ev(3, "memory_read", "env", {"phase": "probe", "coverage": {"files_scanned": 1, "entries_scanned": 2,
                                                                         "bytes_scanned": 10, "sources": ["M"]}})]
    idx3 = EvidenceIndex.load(mkrun(tmp, rows3))
    s3 = sample(anchors=[
        {"id": "A1", "type": "memory_read", "key": "*", "value_regex": "花生", "expect": "present",
         "severity": "hard", "on_violation": "omitted"}])
    o3 = A.evaluate(s3, s3["anchors"][0], idx3, "t1")
    check("U3 env 快照不满足 read 事件锚点（skip 非 pass）", o3.result == R_SKIP)

    # ---- U4 多违规取声明序首条（§5 优先级）——必须是**双违反**才能定序（M8 变异揭露旧版只造
    # 单违反，正反序同果，是伪 catcher）
    s4 = sample(anchors=[
        {"id": "A1", "type": "reply_text", "turn": "s1.t1", "regex": "花生", "expect": "present",
         "severity": "hard", "on_violation": "omitted"},
        {"id": "A2", "type": "reply_text", "turn": "s1.t1", "regex": "拿铁", "expect": "present",
         "severity": "hard", "on_violation": "confused"}])
    idx4 = EvidenceIndex.load(mkrun(tmp, [summary_ok(1), one_agent_reply(2, "好的。")]))  # 两锚点皆违反
    ts4 = decide.decide_task(s4, idx4, "t1")
    check("U4 双违反取声明序首条 → omitted（而非 confused）", ts4.verdict == "omitted")

    # ---- U5 truncated 未命中 → skip（§12）
    fw = ev(2, "file_write", "agent", {"path": "a.md", "op": "create", "after_content": "HEAD",
                                        "truncated": True, "content_sha256": "x", "full_bytes": 99999,
                                        "stored_bytes": 4}, source="file")
    lst = ev(3, "message", "env", {"action": "workspace_listing", "files": 1}, source="system")
    open(os.path.join(tmp, "run", "raw", "t1", "x.jsonl"), "w").close()
    idx5 = EvidenceIndex.load(mkrun(tmp, [summary_ok(1), fw, lst]))
    s5 = sample(anchors=[{"id": "A1", "type": "file_content", "path": "a.md",
                          "content_regex": "尾部的话", "expect": "present", "severity": "soft"}])
    o5 = A.evaluate(s5, s5["anchors"][0], idx5, "t1")
    check("U5 truncated+未命中 = SKIP(truncated_content)", o5.result == R_SKIP and o5.skip_reason == "truncated_content")

    # ---- U6 absent@文件不存在 → PASS，且必带扫描范围（§5 铁律 2）
    idx6 = EvidenceIndex.load(mkrun(tmp, [summary_ok(1), lst]))
    s6 = sample(anchors=[{"id": "A1", "type": "file_content", "path": "不存在.md",
                          "content_regex": "x", "expect": "absent", "severity": "hard",
                          "on_violation": "wrongly_persisted"}])
    o6 = A.evaluate(s6, s6["anchors"][0], idx6, "t1")
    check("U6 absent 文件不存在 = PASS 且有 scan_scope", o6.result == R_PASS and o6.scan_scope)

    # ---- U7 行自检：score 第三值 / verdict-null 违规必须被拒（自守护 #2/#3）
    bad = {"task_id": "t1", "sample_id": "sX", "dimension": "dynamic_update", "status": "ok",
           "verdict": "remembered", "score": 0.5, "score_policy": "hybrid", "decided_by": "rule",
           "evidence_refs": ["ev_001"], "judge_version": None, "judge_model": None,
           "cache_key": None, "judge_error": None, "reason": "x", "skipped_anchors": []}
    probs = check_row(bad, idx6)
    check("U7 score=0.5 被拒（§7 只允许 0/1）", any("第三种值" in p for p in probs))

    # ---- U8 §13 #5 两种 null 合法式
    ok1 = dict(bad, score=None, verdict=None, status="skip", evidence_refs=["ev_001"])
    check("U8a status≠ok + verdict=null 合法", not check_row(ok1, idx6))
    ok2 = dict(bad, score=None, verdict=None, status="ok", judge_error="judge down", evidence_refs=["ev_001"])
    check("U8b ok+judge_error+null 合法", not check_row(ok2, idx6))

    # ---- U9 reply_text 轮次不存在 → EnvError（谓词不可解析不判负）
    from scoring.model import EnvError
    s9 = sample(anchors=[{"id": "A1", "type": "reply_text", "turn": "s9.t9", "regex": "x",
                          "expect": "absent", "severity": "hard", "on_violation": "omitted"}])
    try:
        A.evaluate(s9, s9["anchors"][0], idx4, "t1")
        check("U9 不存在轮次抛 EnvError", False)
    except EnvError:
        check("U9 不存在轮次抛 EnvError", True)

    # ---- U11 must_differ 内容级：把 fixture 原值写回去 = 未更新，不得过（自审发现的旧洞）
    fw_same = ev(4, "file_write", "agent", {"path": "订单.yaml", "op": "modify",
                                            "after_content": "收货地址: 浦西老地址\n"}, source="file")
    s11 = sample(fixture=[{"path": "订单.yaml", "content": "收货地址: 浦西老地址\n"}], anchors=[
        {"id": "A1", "type": "file_content", "path": "订单.yaml", "field_path": "收货地址",
         "content_regex": "浦西", "expect": "present", "severity": "hard",
         "on_violation": "omitted", "must_differ_from_fixture": True}])
    rows11 = [summary_ok(1), one_agent_reply(2, "填好了"),
              ev(3, "memory_read", "env", {"phase": "after", "entries_count": 0,
                   "coverage": {"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0, "sources": []}},
                   source="memory"), fw_same]
    idx11 = EvidenceIndex.load(mkrun(tmp, rows11))
    open(os.path.join(tmp, "run", "raw", "t1", "x.jsonl"), "w").close()
    o11 = A.evaluate(s11, s11["anchors"][0], idx11, "t1")
    check("U11 写回原值+must_differ = VIOLATED（内容级）", o11.result == R_VIOLATED)

    # ---- U13 评分期禁归一化（Q1 裁决）：带连字符的正则必须在原文上匹配；
    # 若评分器默默去连字符/空格，`输出-临时` 类锚点会静默恒不命中 → 本测把它抓红
    snap13 = os.path.join(tmp, "run", "raw", "t1", "u13.jsonl")
    idx_dir = mkrun(tmp, [summary_ok(1), one_agent_reply(2, "嗯"),
                          ev(3, "memory_read", "env", {"phase": "after", "entries_count": 1,
                              "coverage": {"files_scanned": 1, "entries_scanned": 1, "bytes_scanned": 9,
                                           "sources": ["MEMORY.md"]}},
                              raw_ref={"path": "raw/t1/u13.jsonl", "line": 1})])
    with open(snap13, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": "导出目录", "value": "输出-临时", "written_at": None,
                             "written_at_source": None, "source": "MEMORY.md"}, ensure_ascii=False) + "\n")
    idx13 = EvidenceIndex.load(idx_dir)
    s13u = sample(anchors=[{"id": "A1", "type": "memory_entry", "key": "*",
                            "value_regex": "输出\\-临时", "expect": "absent", "severity": "hard",
                            "on_violation": "wrongly_persisted"}])
    o13 = A.evaluate(s13u, s13u["anchors"][0], idx13, "t1")
    check("U13 原文匹配下 absent 正确报 violated（无归一化）", o13.result == R_VIOLATED)

    # ---- U14 内容权威序：真实 file_write 行 > fixture（fixture 不得复活/掩盖末态）
    fw14 = ev(4, "file_write", "agent", {"path": "备注.md", "op": "create",
                                          "after_content": "备注: 放驿站\n"}, source="file")
    idx_dir14 = mkrun(tmp, [summary_ok(1), one_agent_reply(2, "好了"),
                            ev(3, "memory_read", "env", {"phase": "after", "entries_count": 0,
                                "coverage": {"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0,
                                             "sources": []}}, source="memory"), fw14])
    idx14 = EvidenceIndex.load(idx_dir14)
    s14u = sample(fixture=[{"path": "备注.md", "content": "备注: 正常\n"}], anchors=[
        {"id": "A1", "type": "file_content", "path": "备注.md", "content_regex": "驿站",
         "expect": "absent", "severity": "hard", "on_violation": "wrongly_persisted"}])
    o14 = A.evaluate(s14u, s14u["anchors"][0], idx14, "t1")
    check("U14 fixture 不得掩盖真实写行（末态优先）", o14.result == R_VIOLATED)

    # ---- U15 分母漂移守卫（M7 catcher）：同一样本一档可计另档 skip → INCOMPARABLE
    import c2_run
    A_, B_ = "retain_allergy_001", "update_multi_001"
    synth = {}
    for t in c2_run.TIERS:
        synth[t] = {A_: {"status": "ok", "verdict": "omitted"},
                    B_: {"status": "ok", "verdict": "omitted"}, "__dir__": ""}
    synth["forget"][B_] = {"status": "skip", "verdict": None}
    _r, _c, denom = c2_run.aggregate(synth)
    check("U15 分母不等→denom_ok=False（守卫在位）", denom is False)

    # ---- U17 listing 后缀不得认领（模板通知.md ≠ 通知.md；c2 v0.2 首跑抓到的真缺陷回归位）
    lst17 = ev(3, "message", "env", {"action": "workspace_listing", "files": 1}, source="system")
    idx_dir17 = mkrun(tmp, [summary_ok(1), one_agent_reply(2, "嗯"), lst17])
    lraw = os.path.join(idx_dir17, "raw", "t1", "x.jsonl")
    with open(lraw, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"path": "模板通知.md", "sha256": "0" * 64, "size": 9, "mtime_ns": 0}, ensure_ascii=False) + "\n")
    s17 = sample(fixture=[], anchors=[{"id": "A1", "type": "file_content", "path": "通知.md",
                                       "content_regex": "小王", "expect": "absent",
                                       "severity": "hard", "on_violation": "confused"}])
    o17 = A.evaluate(s17, s17["anchors"][0], EvidenceIndex.load(idx_dir17), "t1")
    check("U17 后缀相似文件不被认领（absent 判 PASS 而非 env_error）", o17.result == R_PASS)

    # ---- U10 判官 leak guard：答案标签不得进 prompt（§9 宁炸不泄）----
    try:
        from scoring.judge_client import OpenAICompatJudge
        # 不联网：只测 build_prompt（凭据缺失时构造器会抛，这里用假值绕过）
        jdg = OpenAICompatJudge.__new__(OpenAICompatJudge)
        jdg.allowed = ["remembered"]
        s10 = sample(anchors=[{"id": "A1", "type": "memory_entry", "key": "*",
                               "value_regex": "x", "expect": "present", "severity": "soft"}],
                     expect={"verdict": "remembered", "confusions": ["omitted", "confused"],
                             "notes": "n", "reference_answer": ["r"]})
        soft10 = [AnchorOutcome(anchor_id="A1", atype="memory_entry", expect="present",
                                severity="soft", result=R_PASS, hit_count=1,
                                evidence_refs=["ev_001"])]
        idx10 = EvidenceIndex.load(mkrun(tmp, [summary_ok(1)]))
        # idx10 需含 ev_001（summary 行就是 ev_001）
        p10 = OpenAICompatJudge.build_prompt(jdg, s10, soft10, idx10, "t1")
        check("U10a prompt 不含 expect.verdict 组合式", '{"verdict": "remembered"}' not in p10)
        check("U10b prompt 不含 confusions", "confusions" not in p10)
        check("U10c rubric 历史叙述条款在 prompt 内", "提及历史值 ≠ 混淆" in p10)
    except Exception as exc:  # noqa: BLE001
        check(f"U10 判官模板崩溃: {exc}", False)

    # ---- U16 memory_read 的 query 语义（CR-3 后升级：可观测按 query，不可观测存在性/不判）----
    def agent_read(i, query):
        r = ev(i, "memory_read", "agent", {"phase": "probe",
                                           "coverage": {"files_scanned": 1, "entries_scanned": 1,
                                                        "bytes_scanned": 9, "sources": ["MEMORY.md"]},
                                           "query": query}, source="memory")
        return r
    s16 = sample(anchors=[{"id": "A1", "type": "memory_read", "key": "*", "value_regex": "花生",
                            "expect": "present", "severity": "soft"},
                           {"id": "A2", "type": "memory_read", "key": "*", "value_regex": "花生",
                            "expect": "absent", "severity": "soft"}])
    idxq = EvidenceIndex.load(mkrun(tmp, [summary_ok(1), one_agent_reply(2, "嗯"), agent_read(3, "花生 过敏")]))
    o16a = A.evaluate(s16, s16["anchors"][0], idxq, "t1")
    o16b = A.evaluate(s16, s16["anchors"][1], idxq, "t1")
    check("U16a query 命中→present PASS", o16a.result == R_PASS)
    check("U16b query 命中→absent VIOLATED", o16b.result == R_VIOLATED)
    idxo = EvidenceIndex.load(mkrun(tmp, [summary_ok(1), one_agent_reply(2, "嗯"), agent_read(3, None)]))
    o16c = A.evaluate(s16, s16["anchors"][1], idxo, "t1")
    check("U16c query 不可观测→absent 不可判 SKIP（不白送不误杀）", o16c.result == R_SKIP)
    idxw = EvidenceIndex.load(mkrun(tmp, [summary_ok(1), one_agent_reply(2, "嗯"), agent_read(3, "电影")]))
    o16d = A.evaluate(s16, s16["anchors"][0], idxw, "t1")
    check("U16d query 不对题→present VIOLATED（读了别的≠想起）", o16d.result == R_VIOLATED)

    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        test_repeat_variants(_td)

    # ---- U18 G1：agent_kind=real 且全 run 记忆探针未采到非空 → memory 锚点 SKIP(probe_unverified)
    realman = {"run_id": "r001", "agents": [{"name": "hermes", "agent_kind": "real"}], "contract_version": "v0.1"}
    rows18 = [summary_ok(1), one_agent_reply(4, "好的"),
              ev(2, "memory_read", "env", {"phase": "after", "entries_count": 0,
                  "coverage": {"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0, "sources": []}}, task="hermes-sX"),
              ev(3, "memory_read", "env", {"phase": "before", "entries_count": 0,
                  "coverage": {"files_scanned": 0, "entries_scanned": 0, "bytes_scanned": 0, "sources": []}}, task="hermes-sX")]
    run18 = mkrun(tmp, rows18, manifest=realman)
    idx18 = EvidenceIndex.load(run18)
    check("U18a hermes 被判定为采集未证实", idx18.mem_probe_unverified == {"hermes"})
    s18 = sample(id="sX", anchors=[{"id": "A1", "type": "memory_entry", "key": "*",
                  "value_regex": "花生", "expect": "present", "severity": "hard", "on_violation": "omitted"}])
    o18 = A.evaluate(s18, s18["anchors"][0], idx18, "hermes-sX")
    check("U18b present@未证实探针 = SKIP(probe_unverified)，不再冒充 omitted",
          o18.result == R_SKIP and o18.skip_reason == "probe_unverified")
    # 反向：同 Agent 若别处采到一条非空 → 转正，present@空回 VIOLATED（真失忆仍罚）
    rows18b = [summary_ok(1), one_agent_reply(4, "好的"),
               ev(2, "memory_read", "env", {"phase": "after", "entries_count": 1,
                   "coverage": {"files_scanned": 1, "entries_scanned": 1, "bytes_scanned": 9, "sources": ["memories/MEMORY.md"]}}, task="hermes-sX")]
    run18c = mkrun(tmp, rows18b, manifest=realman)
    idx18c = EvidenceIndex.load(run18c)
    check("U18c 采到非空即豁免（正向控制生效）", idx18c.mem_probe_unverified == set())

    print("\n" + ("❌ 失败 %d 项: %s" % (len(FAILS), FAILS) if FAILS else "✅ 评分器单测全部通过"))
    return 1 if FAILS else 0



def test_repeat_variants(tmpdir):
    """彩排回归锁（自给版，P2-5：不依赖 runs/ 产物）：
    ① score 必须按真实 task_id 出行（含 -rN）；② 行数守护对 manifest↔scored 不齐 exit 2。"""
    import json
    import pathlib
    import subprocess
    # 用模拟器现场造 2 题 run（remember 策略，离线零模型）
    pol = "samples/mock-policies/remember.v0.2.yaml"
    paper = ensure_pilot_jsonl()          # 缺则当场重建并核对冻结 sha（仍不静默 SKIP）
    if not pathlib.Path(pol).is_file():
        raise AssertionError("缺 remember.v0.2.yaml（策略卡片）——不得静默 SKIP")
    rc = subprocess.run([sys.executable, "tools/simulate.py", "--paper", "build/paper-v0.2.jsonl",
                         "--policy", pol, "--run-id", "rep-lock", "--runs-dir", tmpdir,
                         "--samples", "retain_allergy_001,retain_nickname_001"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert rc.returncode == 0, "simulate 造 run 失败: " + (rc.stderr or rc.stdout)[-300:]
    d = pathlib.Path(tmpdir) / "rep-lock"
    ev = d / "evidence.jsonl"
    rows = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    tid = next(r["task_id"] for r in rows if r["task_id"].endswith("retain_allergy_001"))
    with ev.open("a", encoding="utf-8", newline="\n") as fh:
        for r_ in rows:
            if r_.get("task_id") == tid:
                dup = dict(r_)
                dup["task_id"] = tid + "-r1"
                dup["id"] = str(dup.get("id", "x")) + "-dup"
                fh.write(json.dumps(dup, ensure_ascii=False) + "\n")
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    man["tasks"].append(dict(man["tasks"][0], task_id=tid + "-r1"))
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8", newline="\n")
    rc2 = subprocess.run([sys.executable, "tools/score.py", "--run-dir", str(d),
                          "--paper", "build/paper-v0.2.jsonl"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert rc2.returncode == 0, "score 失败: " + (rc2.stderr or "")[-300:]
    got = [json.loads(l)["task_id"] for l in (d / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert (tid + "-r1") in got and got.count(tid) == 1, f"-rN 行被合成 id 吞掉（got {len(got)} 行）"
    # R7：轮次按数值序（r2 在 r10 前；字典序实现会翻）
    if (tid + "-r10") not in got:
        with ev.open("a", encoding="utf-8", newline="\n") as fh:
            for r_ in rows:
                if r_.get("task_id") == tid:
                    dup = dict(r_)
                    dup["task_id"] = tid + "-r10"
                    dup["id"] = str(dup.get("id", "x")) + "-d10"
                    fh.write(json.dumps(dup, ensure_ascii=False) + "\n")
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        man["tasks"].append(dict(man["tasks"][0], task_id=tid + "-r10"))
        man["tasks"] = [x for x in man["tasks"]]
        (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8", newline="\n")
        rc5 = subprocess.run([sys.executable, "tools/score.py", "--run-dir", str(d),
                              "--paper", "build/paper-v0.2.jsonl"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert rc5.returncode == 0, "r10 造形 score 失败: " + (rc5.stderr or "")[-200:]
        seq = [json.loads(l)["task_id"] for l in (d / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        pos = {t: i for i, t in enumerate(seq)}
        assert pos[tid] < pos[tid + "-r1"] < pos[tid + "-r10"], "轮次未数值序（疑似字典序实现）"
    # 行数守护：manifest 多列一条不存在的 task → exit 2
    man["tasks"].append(dict(man["tasks"][0], task_id="mock-no_such_sample_999"))
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8", newline="\n")
    rc3 = subprocess.run([sys.executable, "tools/score.py", "--run-dir", str(d),
                          "--paper", "build/paper-v0.2.jsonl"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert rc3.returncode == 2 and "行数守护" in (rc3.stderr or ""), "行数守护未拦截 manifest↔scored 不齐"
    print("  PASS repeat 变体出行 + 行数守护拦截（自给锁）")


if __name__ == "__main__":
    sys.exit(main())
