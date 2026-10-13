#!/usr/bin/env python3
"""检查项级「判官 vs 真人」标注回路（P2-2 的细粒度版，服务「减少人工审核」这条评审细则）。

为什么另做一个工具：`tools/annotate.py`/`tools/kappa.py` 标的是**任务级五类结果**，
而自动评分的真实产物是**逐检查项**的 supported/contradicted/insufficient + 理由 + 引文。
把检查项硬塞进任务级标签会丢掉判官最有价值的信息（它到底在哪一条要求上判错）。

三段流程（顺序不可颠倒，公开包与私有钥匙目录必须分离）：
  make-worklist   从评分包出题：只给 requirement + 观测证据，**不含判官答案**，
                  并在写盘前断言公开包里不存在 result/rationale/citations 字段
  emit-judge-keys 判官答案写进私有目录（row_key → 判官标签/引文/包指纹）
  validate        校验标注包：行集一致、无重复、标签三选一、理由必填
  agreement       双真人一致率（Cohen's κ + 3×3 混淆 + 分歧清单）；
                  只有在双方都 closed 且提供 --judge-keys 时才追加「真人 vs 判官」两两 κ
确定性：抽样按 on_failure 分层 + 固定 seed，可复算；row_key 绑定 request_sha256，
换一份评分包就换一套行号，防止拿旧标签蒙。

不做什么：不代人填标签；不认证标注者独立性（那是流程约束，不是代码能证明的）；
κ=1 也不等于判官正确（类别稀有度会让 κ 虚高，报告里一并给精确单侧下界）。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import sys
from pathlib import Path

FORMAT_WORKLIST = "okb-check-worklist-v1"
FORMAT_KEYS = "okb-check-judge-keys-v1"
FORMAT_LABELS = "okb-check-labels-v1"
LABELS = ("supported", "contradicted", "insufficient")
ANSWER_FIELDS = ("result", "rationale", "citations", "model", "evidence_refs")
CLOSED_STATES = ("closed", "signed")


def sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def row_key(task_id: str, check_id: str, request_sha: str) -> str:
    return "%s#%s#%s" % (task_id, check_id, request_sha[:12])


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_new(path: Path, text: str) -> None:
    if path.exists():
        raise ValueError("refusing to overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def load_packages(paths):
    """合并多个评分包的判官面：一份盲题包可以跨轮次出题（真人共标包就是 round1+round2）。

    后出现的包不覆盖先出现的同一条题；同一题在两个包里答案不同就直接拒绝——那是
    评分可复现性坏了，不能静默挑一个当答案。
    """
    merged, indexes = [], {}
    for path in paths:
        rows, summary = load_package(Path(path))
        for r in rows:
            key = (r["task_id"], r["check_id"])
            if key in indexes:
                prev = indexes[key]
                if (prev["judge_result"], prev["request_sha256"]) != (r["judge_result"],
                                                                      r["request_sha256"]):
                    raise ValueError("conflicting judge answer across packages for "
                                     + "%s/%s" % key)
                continue
            indexes[key] = r
            merged.append(r)
    return merged, summary


def load_package(score_dir: Path):
    """读评分包的 results + semantic-requests + summary，返回按 (task, check) 索引的判官面。"""
    results = json.loads((score_dir / "results.json").read_text(encoding="utf-8"))
    requests = json.loads((score_dir / "semantic-requests.json").read_text(encoding="utf-8"))
    summary = json.loads((score_dir / "summary.json").read_text(encoding="utf-8"))
    req_by = {(q["task_id"], q["check_id"]): q for q in requests}
    if len(req_by) != len(requests):
        raise ValueError("duplicate (task, check) semantic request")
    rows = []
    for task in results:
        for check in task["layer3"]:
            key = (task["task_id"], check["id"])
            req = req_by.get(key)
            if req is None:
                continue                      # 证据不足没出门题的项，不参与抽题
            rows.append(dict(task_id=task["task_id"], sample_id=task["sample_id"],
                             check_id=check["id"], request=req["request"],
                             request_sha256=req["request_sha256"],
                             judge_result=check.get("result"),
                             judge_rationale=check.get("rationale"),
                             judge_citations=check.get("citations"),
                             # prepare/replay 里判官没答的项也写 insufficient，但带 error 标记；
                             # 它不是判官的三态答案，混进一致率会虚增分母。
                             judge_error=check.get("error"),
                             on_failure=check.get("on_failure"),
                             evidence_tier=check.get("evidence_tier"),
                             package_sha256=summary.get("evidence_policy")))
    if not rows:
        raise ValueError("no semantic checks found in the score package")
    return rows, summary


def stratified_pick(rows: list[dict], count: int, seed: int) -> list[dict]:
    """按 on_failure 轮转抽题，保证少数类（如 wrongly_persisted）不被多数类淹没。"""
    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for r in sorted(rows, key=lambda x: (x["on_failure"] or "", x["task_id"], x["check_id"])):
        buckets[r["on_failure"] or "unspecified"].append(r)
    for b in buckets.values():
        random.Random(seed).shuffle(b)         # seed 只决定同类内顺序，类别顺序固定
    picked: list[dict] = []
    names = sorted(buckets)
    cursors = {n: 0 for n in names}
    while len(picked) < count and any(cursors[n] < len(buckets[n]) for n in names):
        for n in names:
            if cursors[n] < len(buckets[n]) and len(picked) < count:
                picked.append(buckets[n][cursors[n]])
                cursors[n] += 1
    return picked


def public_item(row: dict) -> dict:
    """盲题：只给标注者判得动的东西 —— 要求文本、这题属于哪个证据面、脚本上下文、观测证据。

    两处实测教训：
    - `request` 顶层没有 scope（scope 在 context.scope 里），早先直接取 request.scope 会让
      30 道题的 item.scope 全是 null，标注者看不出这是记忆题、回复题还是产物题；
    - `context.source_binding` 是指纹、`plan_sha256` 是哈希，对人毫无判断价值还占满屏，
      所以只留脚本与初始工件（task）与本题作用域。
    """
    # 判官请求的真实结构是 {requirement, task_context{scope,task,...}, observations, protocol,
    # system_sha256}：作用域在 task_context.scope.scope。实测 226 条请求分布
    # memory_after 89 / agent_reply 80 / file_final 57，标注者必须看到这一个是谁。
    ctx = row["request"].get("task_context") or row["request"].get("context") or {}
    scope = ctx.get("scope") or {}
    obs = [{"evidence_id": o["evidence_id"], "content": o["content"]}
           for o in row["request"].get("observations") or []]
    return dict(requirement=row["request"].get("requirement"),
                scope=scope.get("scope") or row["request"].get("scope"),
                task=ctx.get("task"), observations=obs)


def cmd_make_worklist(args) -> int:
    rows, summary = load_package(Path(args.score_dir).resolve())
    def answered(r):
        return r["judge_result"] in LABELS and not r.get("judge_error")

    pool = [r for r in rows if answered(r)] if args.decided_only else rows
    if not pool:
        print("nothing to sample (decided_only filtered everything)", file=sys.stderr)
        return 2
    picked = stratified_pick(pool, args.count or len(pool), args.seed)
    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists():
        print("public dir must be new: " + str(out_dir), file=sys.stderr)
        return 2
    items, seen = [], set()
    for r in picked:
        key = row_key(r["task_id"], r["check_id"], r["request_sha256"])
        if key in seen:
            raise ValueError("duplicate row_key " + key)
        seen.add(key)
        item = public_item(r)
        # 题面只给 row_key/item/item_sha256。on_failure（"违反判哪一类"）与 evidence_tier
        # 是评分侧元数据：留在题面上会把标注者往"该往哪个类别想"的方向带，因此只进私有侧
        # （emit-judge-keys 与分工台账），公开包彻底不带。
        items.append(dict(row_key=key, item=item, item_sha256=sha256(item)))
    for it in items:                                     # 出盘前最后一道断言
        blob = json.dumps(it, ensure_ascii=False)        # 扫整行，不只扫 item
        for field in ANSWER_FIELDS:
            if ('"%s"' % field) in blob:
                raise ValueError("public item would leak judge answer field: " + field)
    manifest = dict(format=FORMAT_WORKLIST, state="open",
                    label_values=list(LABELS), note="one line per row_key per rater; "
                    "labels must be written without seeing the judge answer or the other rater",
                    source_package=str(Path(args.score_dir).resolve()),
                    e_policy=summary.get("evidence_policy"), mode=summary.get("mode"),
                    pool_size=len(pool), sampled=len(items), seed=args.seed,
                    decided_only=bool(args.decided_only))
    write_new(out_dir / "worklist-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with (out_dir / "worklist.jsonl").open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    # 分布只能从内部 picked 计算：公开题面已刻意不带 on_failure
    hist = dict(collections.Counter(p["on_failure"] for p in picked))
    print(json.dumps(dict(worklist=str(out_dir / "worklist.jsonl"), items=len(items),
                          on_failure_hist=hist, public_row_fields=sorted(items[0]) if items else []),
                     ensure_ascii=False))
    return 0


def cmd_emit_judge_keys(args) -> int:
    worklist = read_jsonl(Path(args.worklist).resolve())
    head = worklist[0]
    if head.get("format") != FORMAT_WORKLIST:
        print("not a check worklist", file=sys.stderr)
        return 2
    rows, _ = load_packages(args.score_dir)
    index = {row_key(r["task_id"], r["check_id"], r["request_sha256"]): r for r in rows}
    out_dir = Path(args.private_dir).resolve()
    if out_dir.exists():
        print("private dir must be new: " + str(out_dir), file=sys.stderr)
        return 2
    keys = []
    for item in worklist[1:]:
        r = index.get(item["row_key"])
        if r is None:
            raise ValueError("worklist row missing from package: " + item["row_key"])
        keys.append(dict(row_key=item["row_key"], judge_result=r["judge_result"],
                         judge_rationale=r["judge_rationale"], judge_citations=r["judge_citations"],
                         judge_error=r.get("judge_error"), request_sha256=r["request_sha256"]))
    if len(keys) != len(worklist) - 1:
        raise ValueError("key rows/items count mismatch")
    write_new(out_dir / "judge-keys.jsonl", "".join(json.dumps(k, ensure_ascii=False) + "\n" for k in keys))
    write_new(out_dir / "README.txt",
              "私有：判官答案。只有在两名标注者都 closed 之后才允许与标签合并。\n"
              "本目录不得进入任何公开包/仓库。\n")
    print(json.dumps(dict(judge_keys=str(out_dir / "judge-keys.jsonl"), rows=len(keys)),
                     ensure_ascii=False))
    return 0


def cmd_validate(args) -> int:
    worklist = read_jsonl(Path(args.worklist).resolve())
    labels = read_jsonl(Path(args.labels).resolve())
    if labels and labels[0].get("format") not in (None, FORMAT_LABELS):
        print("label file format mismatch", file=sys.stderr)
        return 2
    body = worklist[1:]
    want = [i["row_key"] for i in body]
    got = [l.get("row_key") for l in labels]
    errors = []
    if len(got) != len(set(got)):
        errors.append("duplicate row_key in labels")
    if set(got) != set(want):
        errors.append("row_key set mismatch: missing=%d extra=%d"
                      % (len(set(want) - set(got)), len(set(got) - set(want))))
    for l in labels:
        if l.get("label") not in LABELS:
            errors.append("bad label for " + str(l.get("row_key")))
        if not isinstance(l.get("note"), str) or len(l["note"].strip()) < 8:
            errors.append("missing/too-short rationale for " + str(l.get("row_key")))
    head = worklist[0]
    print(json.dumps(dict(valid=not errors, items=len(want), labeled=len(got),
                          state=head.get("state"), errors=errors[:10]), ensure_ascii=False))
    return 0 if not errors else 2


def cohen(pairs, cats):
    n = len(pairs)
    if n == 0:
        return None, None, None
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = collections.Counter([a for a, _ in pairs]), collections.Counter([b for _, b in pairs])
    pe = sum((ca[c] / n) * (cb[c] / n) for c in cats)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return kappa, po, pe


def exact_lower_bound(wins, trials, alpha=0.05):
    """精确单侧 Clopper-Pearson 下界；全一致时闭式 alpha^(1/n)。"""
    if trials == 0:
        return None
    if wins == trials:
        return alpha ** (1.0 / trials)

    from math import comb

    def tail(p):
        # 二项尾概率 P(X >= wins | p)。按定义逐项求和：n 在几百量级足够快且不会算错，
        # 之前那版递推写反了方向（见下），这里不再玩递推。
        return sum(comb(trials, i) * (p ** i) * ((1 - p) ** (trials - i))
                   for i in range(wins, trials + 1))

    # 尾概率随 p 单调递增：太大说明根在左侧，必须收紧 hi（此前的反向二分会把
    # 3/7 报成 0.87 而不是 0.13）
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if tail(mid) > alpha:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def cmd_agreement(args) -> int:
    worklist = read_jsonl(Path(args.worklist).resolve())
    head = worklist[0]
    a_rows = {l["row_key"]: l for l in read_jsonl(Path(args.a).resolve())}
    b_rows = {l["row_key"]: l for l in read_jsonl(Path(args.b).resolve())}
    want = [i["row_key"] for i in worklist[1:]]
    if head.get("state") not in CLOSED_STATES:
        print("worklist state must be one of " + "/".join(CLOSED_STATES) + " before agreement",
              file=sys.stderr)
        return 2
    if set(a_rows) != set(want) or set(b_rows) != set(want):
        print("both raters must cover every row", file=sys.stderr)
        return 2
    pairs = [(a_rows[k]["label"], b_rows[k]["label"]) for k in want]
    cats = [c for c in LABELS if any(c in ab for ab in pairs)] or list(LABELS)
    kappa, po, pe = cohen(pairs, cats)
    n = len(pairs)
    wins = sum(1 for x, y in pairs if x == y)
    out = dict(format="okb-check-agreement-v1", worklist_state=head.get("state"),
               items=n, rater_pair=dict(a=args.a_name, b=args.b_name),
               cohens_kappa=kappa, observed_agreement=po, chance_agreement=pe,
               agreement_exact95_lower_bound=exact_lower_bound(wins, n),
               confusion={a: {b: sum(1 for x, y in pairs if x == a and y == b) for b in LABELS}
                          for a in LABELS},
               disagreements=[dict(row_key=k, a=a_rows[k]["label"], b=b_rows[k]["label"],
                                  a_note=a_rows[k]["note"], b_note=b_rows[k]["note"])
                             for k in want if a_rows[k]["label"] != b_rows[k]["label"]],
               human_vs_judge=None,
               limits=["human-vs-judge is only revealed after both raters are closed",
                       "kappa on rare categories (e.g. wrongly_persisted) is unstable; "
                       "the exact binomial lower bound is reported beside it",
                       "agreement is not correctness: both raters can be wrong together"])
    if args.judge_keys:
        keys = {k["row_key"]: k for k in read_jsonl(Path(args.judge_keys).resolve())}
        if not set(keys) >= set(want):
            print("judge keys must cover every row", file=sys.stderr)
            return 2
        per = {}
        for name, rows in ((args.a_name, a_rows), (args.b_name, b_rows)):
            pairs2 = [(rows[k]["label"], keys[k]["judge_result"]) for k in want
                      if keys[k]["judge_result"] in LABELS and not keys[k].get("judge_error")]
            k2, p2, e2 = cohen(pairs2, [c for c in LABELS if any(c in ab for ab in pairs2)] or list(LABELS))
            per[name] = dict(n=len(pairs2), cohens_kappa=k2, observed_agreement=p2,
                             chance_agreement=e2,
                             agreement_exact95_lower_bound=exact_lower_bound(
                                 sum(1 for x, y in pairs2 if x == y), len(pairs2)),
                             disagreements=[dict(row_key=kk, human=rows[kk]["label"],
                                                 judge=keys[kk]["judge_result"],
                                                 human_note=rows[kk]["note"],
                                                 judge_rationale=keys[kk]["judge_rationale"])
                                            for kk in want
                                            if keys[kk]["judge_result"] in LABELS
                                            and rows[kk]["label"] != keys[kk]["judge_result"]])
        out["human_vs_judge"] = per
        out["limits"].append("judge labels came from a specific model+package; see judge-keys "
                             "sidecar request_sha256 binding")
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        write_new(Path(args.out).resolve(), text)
    print(text, end="")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 第二条人审回路：让人审落在**仪器说不清的那几行**上（不是再要人附和我们自己的结论）
# ══════════════════════════════════════════════════════════════════════════
HARNESS_WORKLIST = "okb-harness-worklist-v1"
HARNESS_FLAGS = "okb-harness-flags-v1"
HARNESS_LABELS = ("agent_never_wrote", "harness_capture_gap", "other_task_residue", "insufficient")
# 题面绝不出现的字段：这些是我们的结论，出现就等于要求人同意仪器
HARNESS_FORBIDDEN = ("attribution", "flags", "suggested_check", "counts_toward_score",
                     "kind", "review_queue", "flag_counts")
# 我们的旗标 → 它隐含的解释。只在**双方交卷之后**才拿来算一致率（私有侧）
# 状态 → 人工解释标签。`write_attributed`（一切正常）故意不给标签：那种行没有需要人复核的主张，
# 把它们混进池子会让"人工 vs 仪器"的一致率被大量平凡行灌水。
ATTRIBUTION_TO_LABEL = {"never_written": "agent_never_wrote",
                        "capture_gap": "harness_capture_gap",
                        "residue_plus_write": "other_task_residue",
                        "residue_cleared": "other_task_residue"}
FLAG_TO_LABEL = {"capture_gap_suspect": "harness_capture_gap",
                 "unattributable_residue": "other_task_residue",
                 "missing_snapshot_pair": "insufficient"}
MAX_VALUE_CHARS = 400


def harness_row_key(task_id: str, obs: dict) -> str:
    return "%s#obs#%s" % (task_id, sha256(obs)[:12])


def harness_item(row: dict, evidence: dict | None) -> dict:
    """公开题面 = 纯观测事实（前/后快照 + 写入事件 + 扫描覆盖范围）。"""
    obs = dict(before_entries=row["before_entries"], after_entries=row["after_entries"],
               write_count=row["writes"], write_keys=sorted(row.get("write_keys") or []))
    if evidence is not None:
        obs["write_events"] = evidence["write_events"]
        obs["before_coverage"] = evidence["before_coverage"]
        obs["after_coverage"] = evidence["after_coverage"]
    return dict(task_id=row["task_id"], sample_id=row["sample_id"], observation=obs)


def read_write_events(run_dir: Path, task_id: str) -> dict:
    """从证据里取该任务的写入事件与两侧快照覆盖范围（人判『观测面漏扫』要看的就是这些）。"""
    out = {"write_events": [], "before_coverage": None, "after_coverage": None}
    path = run_dir / "evidence.jsonl"
    if not path.is_file():
        raise ValueError("missing evidence.jsonl under " + str(run_dir))
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("task_id") != task_id:
            continue
        p = r.get("payload") or {}
        if r.get("type") == "memory_write":
            v = str(p.get("value") or "")
            out["write_events"].append(dict(key=p.get("key"), value=v[:MAX_VALUE_CHARS],
                                            truncated=len(v) > MAX_VALUE_CHARS, via=p.get("via"),
                                            source=p.get("source")))
        elif r.get("type") == "memory_read" and p.get("phase") in ("before", "after"):
            out[p["phase"] + "_coverage"] = dict(entries_count=p.get("entries_count"),
                                                 coverage=p.get("coverage"))
    return out


def cmd_make_harness_worklist(args) -> int:
    rep = json.loads(Path(args.attribution).read_text(encoding="utf-8"))
    rows = rep.get("rows") or []
    if not rows:
        raise ValueError("归因报告里没有逐任务行（rows 为空）")
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    pool, seen, skipped = [], set(), 0
    for row in rows:
        claim = ATTRIBUTION_TO_LABEL.get(row["attribution"]) or \
            FLAG_TO_LABEL.get((row.get("flags") or [None])[0])
        if not claim:
            skipped += 1            # 仪器没做任何需要复核的主张：不进池，避免灌水一致率
            continue
        item = harness_item(row, read_write_events(run_dir, row["task_id"]) if run_dir else None)
        blob = json.dumps(item, ensure_ascii=False)
        for bad in HARNESS_FORBIDDEN:
            if '"%s"' % bad in blob:
                raise ValueError("题面会泄漏仪器结论字段: " + bad)
        key = harness_row_key(row["task_id"], item["observation"])
        if key in seen:
            raise ValueError("duplicate harness row_key " + key)
        seen.add(key)
        pool.append(dict(row_key=key, item=item, item_sha256=sha256(item["observation"]),
                         _flag=row.get("flags") or [], _attribution=row["attribution"],
                         _label=claim))
    picked = pool if not args.count else _harness_sample(pool, args.count, args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    head = dict(format=HARNESS_WORKLIST, state="open", label_values=list(HARNESS_LABELS),
                note="解释这条观测组合最可能是怎么回事；题目只给观测事实，不含仪器结论。"
                     "标签四选一 + 理由必填。不确定就选 insufficient（不算答案，也不许猜）。",
                items=len(picked),
                # 表头允许出现候选解释（那是答题选项）；结论字段只存在于题面行以外——
                #  blindness 断言因此只看题面行，不扫表头（我曾把表头也当泄漏，误报一次）
                source=dict(report_file=args.attribution,
                            run_dir=str(run_dir) if run_dir else None),
                options_are="explanations_of_the_observation_not_verdicts")
    text = json.dumps(head, ensure_ascii=False) + "\n"
    for r in picked:
        text += json.dumps({k: v for k, v in r.items() if not k.startswith("_")},
                           ensure_ascii=False) + "\n"
    write_new(out_dir / "worklist.jsonl", text)
    # 私有侧（旗标映射）单独写，交卷前不给标注者看
    priv = json.dumps([dict(row_key=r["row_key"], instrument_flags=r["_flag"],
                            instrument_attribution=r["_attribution"],
                            instrument_label=r["_label"])
                       for r in picked], ensure_ascii=False, indent=2) + "\n"
    (out_dir / "flags.private.json").write_text(priv, encoding="utf-8", newline="\n")
    (out_dir / "blank.jsonl").write_text(
        "".join(json.dumps(dict(row_key=r["row_key"], label="", note=""), ensure_ascii=False) + "\n"
                for r in picked), encoding="utf-8", newline="\n")
    print(json.dumps(dict(worklist=str(out_dir / "worklist.jsonl"), items=len(picked),
                         pool_rows=len(pool), rows_without_claim=skipped,
                         labels=list(HARNESS_LABELS),
                         sealed=str(out_dir / "flags.private.json"),
                         public_fields=sorted(k for k in picked[0] if not k.startswith("_"))
                         if picked else []),
                   ensure_ascii=False, indent=2))
    return 0


def _harness_sample(pool: list, count: int, seed: int) -> list:
    """确定性抽样：按仪器解释分层，保证每种旗标都被抽到（否则一致率只代表最常见那一类）。"""
    import random
    rng = random.Random(seed)
    by = collections.defaultdict(list)
    for r in pool:
        by[(r["_flag"] or ["none"])[0]].append(r)
    picked, rest = [], []
    for flag, group in sorted(by.items()):
        rng.shuffle(group)
        picked.append(group[0])                       # 每类先保底 1 条
        rest.extend(group[1:])
    rng.shuffle(rest)
    picked.extend(rest)
    return picked[:count]


def cmd_harness_agreement(args) -> int:
    """双真人一致率；两边都 closed 且私有旗标文件在场时，追加"人工 vs 仪器旗标"一致率。"""
    a_path, b_path = Path(args.a), Path(args.b)
    a, b = read_jsonl(a_path), read_jsonl(b_path)
    for name, pkg in ((a_path.name, a), (b_path.name, b)):
        if pkg and pkg[0].get("format") not in (None, HARNESS_WORKLIST):
            raise ValueError("%s: unexpected format %r" % (name, pkg[0].get("format")))
        if pkg[0].get("state") not in CLOSED_STATES:
            raise ValueError("%s 未交卷（state=%r）：不许提前算一致率" % (name, pkg[0].get("state")))
    la = {r["row_key"]: r for r in a[1:]}
    lb = {r["row_key"]: r for r in b[1:]}
    if set(la) != set(lb):
        raise ValueError("两份标注包的行集不同")
    for d in (la, lb):
        for k, r in d.items():
            if r["label"] not in HARNESS_LABELS:
                raise ValueError("行 %s 标签非法: %r" % (k, r["label"]))
            if not str(r.get("note") or "").strip():
                raise ValueError("行 %s 缺理由：无理由的标签不进统计" % k)
    common = sorted(set(la) & set(lb))
    pairs = [(la[k]["label"], lb[k]["label"]) for k in common]
    kappa, po, pe = cohen(pairs, HARNESS_LABELS)
    out = dict(format="okb-harness-agreement-v1", items=len(pairs),
               human_human=dict(observed_agreement=round(po, 4) if po is not None else None,
                                cohens_kappa=kappa, chance_agreement=round(pe, 4) if pe is not None else None,
                                exact95_lower_bound=(round(exact_lower_bound(
                                    sum(1 for x, y in pairs if x == y), len(pairs)), 4) if pairs else None)),
               confusion={a: {b: sum(1 for x, y in pairs if x == a and y == b) for b in HARNESS_LABELS}
                          for a in HARNESS_LABELS},
               disagreements=[dict(row_key=k, a=la[k]["label"], b=lb[k]["label"],
                                   note_a=la[k].get("note"), note_b=lb[k].get("note"))
                              for k in common if la[k]["label"] != lb[k]["label"]],
               limits=["这是**对仪器的复核**，不是对智能体能力的标注",
                       "只有双人 closed 且都写了理由才统计；insufficient 视为答案缺失，不计入一致",
                       "仪器一致率用私有旗标映射（capture_gap→harness_capture_gap 等），"
                       "映射表在源码里公开可见，不是交卷后临时定的",
                       "样本量小的时候一致率的精确下界比点估计更重要"])
    if args.flags and Path(args.flags).is_file():
        flags = {f["row_key"]: f for f in json.loads(Path(args.flags).read_text(encoding="utf-8"))}
        rows = []
        for k in common:
            agreed_human = la[k]["label"] == lb[k]["label"] and la[k]["label"] != "insufficient"
            inst = flags.get(k, {}).get("instrument_label")
            if agreed_human and inst:
                rows.append((la[k]["label"], inst))
        if rows:
            win = sum(1 for h, f in rows if h == f)
            out["human_consensus_vs_instrument"] = dict(
                comparable=len(rows), agreed=win, agreement_rate=round(win / len(rows), 4),
                exact95_lower_bound=round(exact_lower_bound(win, len(rows)), 4),
                by_label={lbl: [sum(1 for h, f in rows if h == lbl and f == lbl),
                                sum(1 for h, f in rows if h == lbl)] for lbl in HARNESS_LABELS},
                note="分母只含『双人一致且非 insufficient』的行；这是仪器有效性的直接证据，"
                     "样本少时只报区间不报结论")
        else:
            out["human_consensus_vs_instrument"] = dict(comparable=0,
                                                        note="双人一致的行不足，无法评估仪器")
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make-worklist", help="从评分包出盲题包（不含判官答案）")
    m.add_argument("--score-dir", required=True)
    m.add_argument("--out-dir", required=True)
    m.add_argument("--count", type=int, default=0, help="0=全量")
    m.add_argument("--seed", type=int, default=20261004)
    m.add_argument("--decided-only", action="store_true",
                   help="只抽判官已给三态结论的项（做 vs-判官 一致率时用）")
    m.set_defaults(func=cmd_make_worklist)

    k = sub.add_parser("emit-judge-keys", help="判官答案写私有目录（最后才用）")
    k.add_argument("--worklist", required=True)
    k.add_argument("--score-dir", required=True, action="append",
                   help="可重复：盲题包跨多个评分包出题时，把每个来源包都传进来")
    k.add_argument("--private-dir", required=True)
    k.set_defaults(func=cmd_emit_judge_keys)

    v = sub.add_parser("validate", help="校验单人标注包")
    v.add_argument("--worklist", required=True)
    v.add_argument("--labels", required=True)
    v.set_defaults(func=cmd_validate)

    g = sub.add_parser("agreement", help="双人一致率；给 --judge-keys 时追加真人 vs 判官")
    g.add_argument("--worklist", required=True)
    g.add_argument("--a", required=True)
    g.add_argument("--b", required=True)
    g.add_argument("--a-name", default="rater-a")
    g.add_argument("--b-name", default="rater-b")
    g.add_argument("--judge-keys", default=None)
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_agreement)

    h = sub.add_parser("make-harness-worklist",
                       help="从归因报告出『仪器说不清』的盲题包（题面只给观测事实）")
    h.add_argument("--attribution", required=True, help="baseline-attribution 的 JSON 报告")
    h.add_argument("--run-dir", default=None, help="跑批目录：附上写入事件与快照覆盖范围")
    h.add_argument("--out-dir", required=True)
    h.add_argument("--count", type=int, default=0, help="0=全量")
    h.add_argument("--seed", type=int, default=20261005)
    h.set_defaults(func=cmd_make_harness_worklist)

    q = sub.add_parser("harness-agreement",
                       help="双人对仪器的解释一致率；给 --flags 时追加『人工 vs 旗标』一致率")
    q.add_argument("--a", required=True)
    q.add_argument("--b", required=True)
    q.add_argument("--flags", default=None, help="私有 flags.private.json（双方交卷后才用）")
    q.add_argument("--out", default=None)
    q.set_defaults(func=cmd_harness_agreement)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError, KeyError) as exc:
        print("rejected: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
