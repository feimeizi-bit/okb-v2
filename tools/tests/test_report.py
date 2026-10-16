#!/usr/bin/env python3
"""report.py v2 手算对拍 + 三轮验收对抗回归位（M1~M7 全落）。
每个对抗构造都来自 review/momus 的实测攻击，逐条标注其契约出处。
用法：python tools/tests/test_report.py"""
from __future__ import annotations
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import report as RP  # noqa: E402

FAILS = []
NCHK = [0]


def check(name, cond):
    NCHK[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def row(sid, v, status="ok", reason="r", **kw):
    r = {"sample_id": sid, "status": status, "verdict": v, "reason": reason,
         "evidence_refs": ["ev_1"], "task_id": f"ag1-{sid}"}
    r.update(kw)
    return r


def mk_run(tmp, name, rows, man=None):
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "verdicts.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    json.dump(man or {"suite_version": "test", "contract_version": "v0.1"},
              open(os.path.join(d, "manifest.json"), "w", encoding="utf-8"))
    # trace 需要的最小 evidence
    with open(os.path.join(d, "evidence.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"id": "ev_1", "seq": 0, "run_id": "r", "ts": "t", "task_id": "t",
                             "session_id": None, "turn": None, "source": "memory",
                             "type": "memory_read", "actor": "env",
                             "payload": {"phase": "after", "coverage": {}},
                             "raw_ref": {"path": "raw/t/a.jsonl", "line": 1},
                             "pii_hits": []}, ensure_ascii=False) + "\n")
    os.makedirs(os.path.join(d, "raw", "t"), exist_ok=True)
    open(os.path.join(d, "raw", "t", "a.jsonl"), "w").close()
    return d


PAPER = [
    {"id": "sA", "dimension": "long_term_retention", "expect": {"verdict": "remembered"}},
    {"id": "sB", "dimension": "long_term_retention", "expect": {"verdict": "remembered"}},
    {"id": "sC", "dimension": "boundary_recognition",
     "expect": {"verdict": "remembered", "boundary_kind": "withhold"}},
]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="okb_report2_")

    # ── 基线：全 ok 一行 + skip(guard) 一行（M1/M4 的干净形）──
    base = [row("sA", "remembered"),
            row("sB", None, "skip", "guard#5：全 hard 通过但有 hard 锚点不可判", evidence_refs=["ev_1"]),
            row("sC", "wrongly_persisted")]
    d = mk_run(tmp, "runBase", base)
    rep, probs = RP.build_report([d], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("基线无问题", not probs)
    ltr = rep["runs"][0]["dims"]["long_term_retention"]
    check("M1a skip 行剔出分母并入剔除公示（带归因）",
          ltr["n_scored"] == 1 and any("sB(skip)" in x for x in ltr["excluded"]))
    bnd = rep["runs"][0]["dims"]["boundary_recognition"]
    check("wp 行留在分母（§7:244 不抵分不剔除）", bnd["n_scored"] == 1 and bnd["wp_rate"] == 1.0)
    check("M3c 覆盖率分母=paper 该维样本数", abs(ltr["coverage"] - 0.5) < 1e-9)
    rep_j = RP.build_report([d], PAPER, thr_n=100, thr_cov=0.99, trace=0, judged_view=False)[0]
    check("P2-4 note 随实际阈值渲染", "n≥100" in RP.to_md(rep_j) and "n≥10∧" not in RP.to_md(rep_j))

    # ── M1 对抗三构造（timeout_with_verdict / mixed_je / score_conflict）──
    d2 = mk_run(tmp, "runT", [dict(row("sA", "remembered", "timeout"), status="timeout"),
                              dict(row("sB", "remembered", "timeout"), status="timeout"),
                              row("sC", "remembered")])
    _, probs2 = RP.build_report([d2], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("M1b status=timeout 带 verdict → §13#5 违反进 problems（可失败）",
          bool(probs2) and any("§13#5" in p for p in probs2))

    d3 = mk_run(tmp, "runJE", [row("sA", "remembered", judge_error="timeout"),
                               row("sB", "remembered"), row("sC", None, "skip", "guard#5")])
    _, probs3 = RP.build_report([d3], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("M1c judge_error∧verdict 并存 → 契约拒绝", any("judge_error" in p for p in probs3))
    # skip 无 reason 也要拒（momus bare_skip）
    d3b = mk_run(tmp, "runBS", [row("sA", None, "skip", reason="", evidence_refs=[]),
                                row("sB", "remembered"), row("sC", "remembered")])
    _, probs3b = RP.build_report([d3b], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("bare skip（无 reason/refs）→ §7口径2 违反被拒", any("skip" in p for p in probs3b))

    # ── M2 verdict 越域 ──
    d4 = mk_run(tmp, "runBad", [row("sA", "memorized"), row("sB", "remembered"), row("sC", "remembered")])
    _, probs4 = RP.build_report([d4], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("M2 verdict 越域被拒且点名", any("越域" in p and "sA" in p for p in probs4))

    # ── M3 重复键 / 空文件 ──
    dup = [row("sA", "remembered"), row("sB", "remembered"), row("sC", "remembered"),
           row("sA", "omitted")]  # 同 task_id
    d5 = mk_run(tmp, "runDup", dup)
    _, probs5 = RP.build_report([d5], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("M3a 重复 task 键被拒", any("重复" in p for p in probs5))
    d6 = mk_run(tmp, "runEmpty", [])
    _, probs6 = RP.build_report([d6], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("M3b 空 verdicts 被拒（不洗白）", bool(probs6))

    # ── P1-4/P1-5 多 Agent + repeat 后缀 ──
    multi = ([row("sA", "remembered", task_id="agent1-sA"), row("sB", "omitted", task_id="agent1-sB"),
              row("sC", "remembered", task_id="agent1-sC")]
             + [row("sA", "confused", task_id="agent2-sA"), row("sB", "remembered", task_id="agent2-sB"),
                row("sC", "remembered", task_id="agent2-sC")]
             + [row("sA", "remembered", task_id="agent1-sA-r2"), row("sB", "remembered", task_id="agent1-sB-r2"),
                row("sC", "remembered", task_id="agent1-sC-r2")])
    dm = mk_run(tmp, "runM", multi)
    repm, probm = RP.build_report([dm], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("P1-4 repeat 后缀不误判 agent（无缺行）", not probm)
    labels = [r["label"] for r in repm["runs"]]
    check("P1-5 维度×Agent 双轴（agent1/agent2 分开、r2 归 agent1）",
          len(labels) == 2 and all(any(x in lb for x in ("agent1", "agent2")) for lb in labels))
    a1 = next(r for r in repm["runs"] if r["label"].endswith("agent1"))
    l1 = a1["dims"]["long_term_retention"]
    # v3 语义（复审 N1 指实后）：acc 按行（repeat 全保留），coverage 按去重可测样本/该维样本数
    # v4 二阶段：sA 两轮全 rem→1.0；sB [omitted, remembered]→0.5 → 维度 acc=(1+0.5)/2=0.75
    check("N1 v4 二阶段：n_scored=2 样本 · n_rows=4 · acc=0.75 · coverage=1.0",
          l1["n_scored"] == 2 and l1["n_rows"] == 4 and abs(l1["acc"] - 0.75) < 1e-9
          and l1["coverage"] == 1.0)
    # 折叠专测：r1 remembered / r2 omitted → 行 acc 必须反映两轮（0.5），非 last-wins(0 或 1)
    rr, rp = RP.build_report([mk_run(tmp, "runR", [
        row("sA", "remembered", task_id="ag9-sA"), row("sB", "remembered", task_id="ag9-sB"),
        row("sC", "remembered", task_id="ag9-sC"),
        row("sA", "omitted", task_id="ag9-sA-r2"), row("sB", "omitted", task_id="ag9-sB-r2"),
        row("sC", "remembered", task_id="ag9-sC-r2")])], PAPER, thr_n=1, thr_cov=0.5,
        trace=0, judged_view=False)
    lr = rr["runs"][0]["dims"]["long_term_retention"]
    check("N1b repeat 全计入：两样本各半对 → acc=0.5", not rp and lr["n_scored"] == 2
          and lr["n_rows"] == 4 and abs(lr["acc"] - 0.5) < 1e-9)
    # review#2/mu9：非等轮次判别例（行口径 1/3 vs 样本口径 1/2）
    rx = RP.build_report([mk_run(tmp, "runUX", [
        row("sA", "omitted", task_id="a9-sA"), row("sA", "omitted", task_id="a9-sA-r2"),
        row("sB", "remembered", task_id="a9-sB"), row("sC", "remembered", task_id="a9-sC")])],
        PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)[0]
    lx = rx["runs"][0]["dims"]["long_term_retention"]
    check("mu9 击杀：非等轮次 acc=样本均值 0.5（行口径会是 0.333）· cov=1.0",
          abs(lx["acc"] - 0.5) < 1e-9 and lx["coverage"] == 1.0 and lx["n_rows"] == 3)
    # mu2 击杀：直测 scorable 状态分支（行级 gate 之外的单元位）
    check("mu2 击杀：scorable(timeout∧remembered)=False",
          RP.scorable({"status": "timeout", "verdict": "remembered"})[0] is False)
    # mu2 存活位（momus）：timeout ∧ verdict=null ∧ 无 je = 合法行，剔分母不报错
    rt_to, rp_to = RP.build_report([mk_run(tmp, "runTO", [
        row("sA", None, "timeout"), row("sB", "remembered"), row("sC", "remembered")])],
        PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("N4 timeout+null 合法且剔出分母", not rp_to
          and rt_to["runs"][0]["dims"]["long_term_retention"]["n_scored"] == 1)
    # P2-1：无 task_id 的完整数据不得退化成一堆伪缺行
    dn = mk_run(tmp, "runNT", [dict(row("sA", "remembered"), task_id=None),
                               dict(row("sB", "remembered"), task_id=None),
                               dict(row("sC", "remembered"), task_id=None)])
    _, pn = RP.build_report([dn], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("无 task_id 单组回退（伪缺行=0）", not pn)

    # ── M5 敏感性三档真实重算 ──
    g5rows = [row("sA", None, "skip", "guard#5：不可判"), row("sB", None, "skip", "guard#5：不可判"),
              row("sC", "omitted")]
    dg = mk_run(tmp, "runG5", g5rows)
    rg, _ = RP.build_report([dg], PAPER, thr_n=1, thr_cov=0.5, sensitivity=True,
                            trace=0, judged_view=False)
    arms = rg["sensitivity"]
    keys = list(arms)
    main_ov = arms[keys[0]][0]["overall"]
    g5_ov = arms["guard#5=off（完整重算）"][0]["overall"]
    check("M5 guard#5=off 行独立重算总分（与主口径不同）", main_ov != g5_ov or main_ov is None)

    # ── M4 trace 按维度分组计数 ──
    rt, _ = RP.build_report([d], PAPER, thr_n=1, thr_cov=0.5, trace=2, judged_view=False)
    tr = rt["runs"][0]["trace"]
    check("M4 trace 每维抽 N（引用数=抽中行 refs 和且>单 run 退化值）",
          tr["checked"] == tr["total_refs"] == 3 and not tr["broken"])

    # ── M6 md 渲染列数对齐 ──
    md = RP.to_md(rep)
    lines_md = md.split("\n")
    pairs_ = [(i, ln) for i, ln in enumerate(lines_md) if ln.startswith("| 维度 ")]
    check(f"M6 全部 {len(pairs_)} 个表块表头/分隔列数相等", pairs_ and all(
        ln.count("|") == lines_md[i + 1].count("|") for i, ln in pairs_))
    check("M6b 总分数值渲染无裸 None", "None" not in md)

    # 单位守卫连发 v2（review 复验：上一批 4 变异存活，本批逐一钉死）
    # A. cov/low_conf 单位锚：sA 三轮可计 + sB/sC 全剔 → 样本 cov=1/3、行 cov=3/3
    rca, _p = RP.build_report([mk_run(tmp, "runCA", [
        row("sA", "remembered", task_id="ca-sA"), row("sA", "remembered", task_id="ca-sA-r2"),
        row("sA", "remembered", task_id="ca-sA-r3"),
        row("sB", None, "timeout", task_id="ca-sB"), row("sC", None, "timeout", task_id="ca-sC")])],
        PAPER, thr_n=1, thr_cov=0.6, trace=0, judged_view=False)
    lc = rca["runs"][0]["dims"]["long_term_retention"]
    check("锚A cov=样本口径 1/2=0.5（行口径会是 1.0）→ low_conf True",
          abs(lc["coverage"] - 0.5) < 1e-9 and lc["n_scored"] == 1 and lc["n_rows"] == 3
          and lc["low_confidence"] is True)
    lb = rca["runs"][0]["dims"]["boundary_recognition"]
    check("锚A2 timeout 裸行只剔分母不拒整报（N3 收缩，review#3）", not _p and lb["n_scored"] == 0)
# N2 直测：sid 自带 -r2 不再拆双 agent
    ag, rp_ = RP.parse_task("adv-weird-r2", "weird-r2")
    check("N2 parse_task 直测（agent=adv，无轮次）", ag == "adv" and rp_ == "")
    ag2, rp2 = RP.parse_task("agent1-sA-r2", "sA")
    check("N2b 常规 repeat 解析", ag2 == "agent1" and rp2 == "-r2")
    # N3 直测：env_error 无 reason/refs 被拒
    _, pn3 = RP.build_report([mk_run(tmp, "runEN", [
        {"sample_id": "sA", "status": "env_error", "verdict": None, "reason": "",
         "evidence_refs": [], "task_id": "e-sA"},
        row("sB", "remembered", task_id="e-sB"), row("sC", "remembered", task_id="e-sC")])],
        PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    check("N3 env_error 裸行被契约拒", any("env_error" in p for p in pn3))
    # N12 直测：judged 文件坏行被契约拒
    dj = mk_run(tmp, "runJD", [row("sA", "remembered"), row("sB", "remembered"),
                              row("sC", "remembered")])
    with open(os.path.join(dj, "verdicts.judged.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"sample_id": "sA", "status": "ok", "verdict": "memorized",
                             "reason": "x", "evidence_refs": ["ev_1"], "task_id": "ag1-sA"},
                            ensure_ascii=False) + "\n")
    _, pj12 = RP.build_report([dj], PAPER, thr_n=1, thr_cov=0.5, trace=0)
    check("N12 judged 坏行过契约", any("judged" in p and "越域" in p for p in pj12))

    # 锚C：judged 双样本不等轮（行口径 2/3 vs 样本 0.5）
    dj2 = mk_run(tmp, "runJ2", [row("sA", "remembered"), row("sB", "remembered"),
                                row("sC", "remembered")])
    with open(os.path.join(dj2, "verdicts.judged.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for rr_ in ({"sample_id": "sA", "status": "ok", "verdict": "remembered", "reason": "r",
                     "evidence_refs": ["ev_1"], "task_id": "ag1-sA"},
                    {"sample_id": "sA", "status": "ok", "verdict": "remembered", "reason": "r",
                     "evidence_refs": ["ev_1"], "task_id": "ag1-sA-r2"},
                    {"sample_id": "sB", "status": "ok", "verdict": "confused", "reason": "r",
                     "evidence_refs": ["ev_1"], "task_id": "ag1-sB"}):
            fh.write(json.dumps(rr_, ensure_ascii=False) + "\n")
    rcj, _cj = RP.build_report([dj2], PAPER, thr_n=1, thr_cov=0.5, trace=0)
    jc = rcj["runs"][0]["dims"]["long_term_retention"]["judged"]
    check("锚C judged 样本口径 0.5（行口径会是 2/3）",
          abs(jc["acc"] - 0.5) < 1e-9 and jc["n_scored"] == 2)

    # 锚E：低置信单位——非等轮（2 样本 4 行）+ thr_n=3 卡在两单位之间
    re_, _pe = RP.build_report([mk_run(tmp, "runE", [
        row("sA", "remembered", task_id="e2-sA"), row("sA", "remembered", task_id="e2-sA-r2"),
        row("sA", "remembered", task_id="e2-sA-r3"),
        row("sB", "remembered", task_id="e2-sB"), row("sC", "remembered", task_id="e2-sC")])],
        PAPER, thr_n=3, thr_cov=0.0, trace=0, judged_view=False)
    ltrE = [d for d in re_["runs"][0]["dims"].values() if d["n_rows"] == 4][0]
    check("锚E low_confidence 随样本不随行（2样本<3 → True）",
          ltrE["n_scored"] == 2 and ltrE["n_rows"] == 4 and ltrE["low_confidence"] is True)

    # 锚D：problems >20 时 stderr 出「共 N 条」
    import io, contextlib
    dp = os.path.join(tmp, "paper.jsonl")
    with open(dp, "w", encoding="utf-8", newline="\n") as fh:
        for s_ in PAPER:
            fh.write(json.dumps(s_, ensure_ascii=False) + "\n")
    many = [dict(row("sA", "bogus"), task_id=f"a{i:02d}-sA") for i in range(21)]
    d43 = mk_run(tmp, "runMANY", many)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc_d = RP.main(["--runs", d43, "--paper", dp])
    check("锚D problems>20 打印总数", rc_d == 1 and "共" in buf.getvalue())
    # withhold/judged 样本口径（同样本两轮半 wp → 0.5；行口径 1/3 或 2/3）
    rwm = mk_run(tmp, "runWM", [row("sA", "remembered"),
                                row("sB", "remembered"),
                                {"sample_id": "sC", "status": "ok", "verdict": "wrongly_persisted",
                                 "reason": "r", "evidence_refs": ["ev_1"], "task_id": "ag1-sC"},
                                {"sample_id": "sC", "status": "ok", "verdict": "confused",
                                 "reason": "r", "evidence_refs": ["ev_1"], "task_id": "ag1-sC-r2"}])
    rw, _ = RP.build_report([rwm], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    # 双 withhold 样本不等轮次：sC 两轮 [wp,wp]=1.0 + 第二样本需构造——PAPER 仅一 bnd 样本，
    # 改用行/样本可分辨形：sC 两轮 [wp, confused] → 样本口径 0.5；行口径也是 0.5 → 不判别！
    # 于是扩 PAPER 造第二 withhold 样本 sD：
    global_p2 = PAPER + [{"id": "sD", "dimension": "boundary_recognition",
                          "expect": {"verdict": "remembered", "boundary_kind": "withhold"}}]
    rw2, _w2 = RP.build_report([mk_run(tmp, "runWM2", [
        row("sA", "remembered", task_id="w2-sA"), row("sB", "remembered", task_id="w2-sB"),
        {"sample_id": "sC", "status": "ok", "verdict": "wrongly_persisted", "reason": "r",
         "evidence_refs": ["ev_1"], "task_id": "w2-sC"},
        {"sample_id": "sC", "status": "ok", "verdict": "wrongly_persisted", "reason": "r",
         "evidence_refs": ["ev_1"], "task_id": "w2-sC-r2"},
        {"sample_id": "sD", "status": "ok", "verdict": "confused", "reason": "r",
         "evidence_refs": ["ev_1"], "task_id": "w2-sD"}])],
        global_p2, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    wh = rw2["runs"][0]["dims"]["boundary_recognition"]["withhold"]
    # 样本口径 (1.0 + 0)/2 = 0.5；行口径 2/3
    check("锚B withhold 双样本不等轮：样本 0.5 vs 行 0.667",
          abs(wh["wp_rate_within_withhold"] - 0.5) < 1e-9 and wh["n_scored"] == 2)
    # 部分轮剔除公示可见
    rpp = mk_run(tmp, "runPP", [row("sA", "remembered"),
                                {"sample_id": "sA", "status": "timeout", "verdict": None,
                                 "reason": "r", "evidence_refs": ["ev_1"], "task_id": "ag1-sA-r2"},
                                row("sB", "remembered"), row("sC", "remembered")])
    rpz, _pp = RP.build_report([rpp], PAPER, thr_n=1, thr_cov=0.5, trace=0, judged_view=False)
    exl = rpz["runs"][0]["dims"]["long_term_retention"]["excluded"]
    check("部分轮剔除公示带「保留N轮」", any("保留1轮" in x for x in exl))

    print("\n" + (f"❌ 失败 {len(FAILS)}: {FAILS}" if FAILS else f"✅ report v4.1 对拍全部通过（{NCHK[0]} 项断言）"))
    return 1 if FAILS else 0



if __name__ == "__main__":
    sys.exit(main())
