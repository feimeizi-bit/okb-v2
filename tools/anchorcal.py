#!/usr/bin/env python3
"""anchorcal —— 预注册 §3.4 红线4「锚点可执行性校准」：把交付卷每条锚点谓词放到**真实证据**上跑，
量出「正则永不命中」的死锚，并给扫描范围证据（CONTRACT §5 铁律 2）。

## 为什么这个工具的核心是"把两件事拆开"

第一版把"在真实语料上零命中"一律报成死锚，结果 14 条嫌疑里绝大多数是**冤枉**的：
  * `reuse_memory_only_001:A2a`（`周报.md` 里要有「亮点」）—— 真实批里 Agent **根本没写那个文件**
    （只在回复里给了结构），而「亮点」这个词在同一次真实回复里明明白白出现过；
  * `retain_allergy_001:A1`（记忆面 `value_regex: 花生`）—— openclaw 的快照 13 条全是通用条目、
    hermes 的记忆探针**整轮未证实**（`EvidenceIndex.mem_probe_unverified`），
    即"读不到"，而 CONTRACT §5 明写：读不到 ≠ 没记住；
  * `similar_wifi_001:A1`（`网络指引.md` 要有 `Yue2026office`）—— 这道题**不在那个真实批里**，
    别的题的产物当然不含它的口令。

所以本工具把两个正交的问题分开量：
  ① **谓词可实现性**（`realizable`）：这条正则在**全量真实语料**（回复 ∪ 记忆条目 ∪ 产物正文）里
     能不能命中至少一条？不能 ⇒ `suspect_dead_predicate`，这才是红线4 说的"正则永不命中的死锚"。
  ② **该题证据到位没有**（`evidence_scope`）：本卷这道题在那个真实批里有没有对应任务、
     产物路径有没有被真的写出来、记忆探针是否被证实。缺 ⇒ 记成对应的 `undecidable_*` /
     `evidence_absent_*`，**一律不折算成通过，也不指控锚点**。

## 三条硬口径（改之前先读 CONTRACT §5）
1. 语料只来自真实 run（`--run-dir` 的 evidence.jsonl + raw/ 快照 + file_write 正文）。
   不合成"看起来像"的字符串充当真实证据 —— 那是自己给自己发合格证。
2. 池子不够大不许判通过：候选语料 < `--min-pool` ⇒ `undecidable_small_pool`。
   与「`absent` 锚点在空集上记不可判而非通过」同源：采不到 ≠ 没发生。
3. 按 `expect` 极性分开判，两者的"死"含义相反：
   `present` + 谓词不可实现 ⇒ 死锚嫌疑（该题只能失败，锚点误伤第一嫌疑）；
   `absent` + 谓词不可实现 ⇒ `unverified_negative`（从不触发，分不清正确克制与死判据）。

## 顺带量出来的一类数据设计风险（不是正则问题，但更值钱）
`path_never_produced`：这道题**在真实批里有任务**，但锚点钉的那个产物路径 Agent 一次都没写出来
（实测例：锚点钉 `周报.md`，Agent 真实落盘叫 `周报-2026-09-25.md`）。这种 hard+present 锚点
在真实行为下**只能失败**，与正则写得好不好无关 —— 属 E119/W207 那一族"无有效正向门槛"的反面。

不做什么：不评分、不产 verdict、不改卷、不发网络请求；只读 + 写一份凭据。
退出码：0 = 校准跑完（**不等于**零死锚，读数看凭据）；2 = 输入缺失/凭据漂移。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scoring.index import EvidenceIndex                        # noqa: E402
from scoring.predicates import compile_rx, field_value, hit_entries, scalarize   # noqa: E402
from scoring.model import EnvError                             # noqa: E402

FORMAT = "okb-anchorcal-v2"
EVENT_TYPES = ("memory_read", "memory_write", "tool_call")
PARSEABLE = (".json", ".yaml", ".yml")
# 恒真判据的阈值：不用"== 全中"，因为真实语料里只要有一条空回复，`.+` 就不再恒等，
# 于是所有结构锚点一条都标不出来（第一版实测：21 → 0）。阈值写进凭据，可核可改。
TAUTOLOGY_RATE = 0.99


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_paper(path: pathlib.Path) -> list[dict]:
    if path.suffix in (".yaml", ".yml"):
        from loader import load_any
        return load_any(str(path))[1]
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class Corpus:
    """真实证据语料：全局池（可实现性）+ 按样本切分的池（证据到位性）。"""

    def __init__(self, idx: EvidenceIndex):
        self.idx = idx
        self.agents = [a.get("name", "") for a in idx.manifest.get("agents", [])]
        self.probe_unverified = set(idx.mem_probe_unverified or ())
        self.tasks = [t["task_id"] for t in (idx.manifest.get("tasks") or [])]
        self.task_of_sample: dict[str, list[str]] = collections.defaultdict(list)
        for t in self.tasks:
            self.task_of_sample[self.sample_of(t)].append(t)
        self.replies: dict[str, list[str]] = collections.defaultdict(list)     # sample -> texts
        self.entries: dict[str, list[dict]] = collections.defaultdict(list)    # sample -> entries
        self.files: dict[str, list[dict]] = collections.defaultdict(list)      # sample -> {path,content}
        self.listing: dict[str, set] = collections.defaultdict(set)            # sample -> 真实工作区路径
        self.listing_available: dict[str, bool] = {}
        self.entry_probe_ok: dict[str, bool] = {}
        for t in self.tasks:
            s = self.sample_of(t)
            for r in idx.agent_messages(t):
                self.replies[s].append(str((r.get("payload") or {}).get("text", "")))
            for ph in ("before", "after"):
                snap = idx.snapshot(t, ph)
                if snap:
                    self.entries[s].extend(idx.snapshot_entries(snap))
            for r in idx.rows:
                if r.get("task_id") == t and r.get("type") == "file_write":
                    c = (r.get("payload") or {}).get("after_content")
                    if isinstance(c, str):
                        self.files[s].append({"path": str((r.get("payload") or {}).get("path", "")),
                                              "content": c, "task_id": t})
            # 该任务的记忆面是否可信：agent 探针未证实 ⇒ 记忆池不能当"真实形状"的充分样本
            ag = idx.agent_of(t)
            self.entry_probe_ok[t] = ag not in self.probe_unverified
            # 工作区清单：路径存在与否的权威来源（file_write 只覆盖"这轮写过"的文件，
            # fixture 与上一轮留下的文件都不在其中 —— 第一版就是因此冤枉了 fixture 路径）
            got = idx.listing(t)
            smp = self.sample_of(t)
            if got is None:
                self.listing_available.setdefault(smp, False)
            else:
                entries, _eid = got
                self.listing_available[smp] = True
                self.listing[smp].update(str(e.get("path", "")) for e in entries)

    def sample_of(self, task_id: str) -> str:
        best = ""
        for n in self.agents:
            if n and (task_id == n or task_id.startswith(n + "-")) and len(n) > len(best):
                best = n
        rest = task_id[len(best) + 1:] if best and task_id.startswith(best + "-") else task_id
        return rest.rsplit("-r", 1)[0] if rest.rsplit("-r", 1)[-1].isdigit() else rest

    # ---- 全局池（可实现性用：最宽松，任何真实串都算候选）----
    @property
    def all_texts(self) -> list[str]:
        out = [t for v in self.replies.values() for t in v]
        out += [f["content"] for v in self.files.values() for f in v]
        for v in self.entries.values():
            for e in v:
                out += [str(e.get("key", "")), str(e.get("value", ""))]
        return out

    def scan_scope(self) -> dict:
        return {"tasks": len(self.tasks), "samples_with_evidence": len(self.task_of_sample),
                "agents": self.agents,
                "memory_probe_unverified_agents": sorted(self.probe_unverified),
                "agent_reply_texts": sum(len(v) for v in self.replies.values()),
                "memory_entries": sum(len(v) for v in self.entries.values()),
                "file_writes": sum(len(v) for v in self.files.values()),
                "workspace_listings_available": sum(1 for v in self.listing_available.values() if v),
                "workspace_paths_listed": sum(len(v) for v in self.listing.values()),
                "global_candidate_strings": len(self.all_texts),
                "event_rows_by_type_and_via": dict(collections.Counter(
                    "%s/%s" % (r["type"], (r.get("payload") or {}).get("via"))
                    for r in self.idx.rows if r.get("type") in EVENT_TYPES))}


def _rx_of(anchor: dict):
    raw = anchor.get("regex") or anchor.get("value_regex") or anchor.get("content_regex")
    return (str(raw), compile_rx(str(raw))) if raw not in (None, "") else (None, None)


def calibrate(anchor: dict, sample: dict, corp: Corpus, min_pool: int) -> dict:
    sid = str(sample.get("id"))
    atype = str(anchor.get("type"))
    expect = str(anchor.get("expect"))
    out = {"type": atype, "expect": expect, "severity": anchor.get("severity"),
           "on_violation": anchor.get("on_violation"), "path": anchor.get("path"),
           "field_path": anchor.get("field_path"),
           "predicate": None, "evidence_scope": None, "realizable": None,
           "own_pool_size": 0, "own_matches": 0, "global_hits": 0,
           "status": None, "detail": ""}
    if atype in EVENT_TYPES:
        out["status"] = "not_calibratable_event_anchor"
        out["detail"] = ("事件类锚点依赖 via=event 实时采集；CONTRACT §5 规定 probe_diff 推出的写入"
                         "不得作 absent 事件锚点的证据 ⇒ 不用终态快照冒充事件语料")
        return out

    tasks = corp.task_of_sample.get(sid) or []
    out["evidence_scope"] = "own_task" if tasks else "no_matching_task"

    # ① 本样本自己的池（按锚点类型取对应种类）
    if atype == "memory_entry":
        own_entries = [e for t in tasks for e in corp.entries.get(t.replace(corp.sample_of(t), "") or t, [])]
        # 上面那行不可靠：entries 按 sample 聚合，直接取 sample
        own_entries = [e for e in corp.entries.get(sid, [])]
        probe_ok = any(corp.entry_probe_ok.get(t, False) for t in tasks)
        out["own_pool_size"] = len(own_entries)
        out["memory_probe_verified_for_this_sample"] = bool(probe_ok) if tasks else None
        try:
            out["own_matches"] = len(hit_entries(own_entries, anchor))
        except EnvError as exc:
            out["status"], out["detail"] = "predicate_error", str(exc)
            return out
        raw, rx = _rx_of(anchor)
        out["predicate"] = raw or anchor.get("key_regex")
    elif atype == "reply_text":
        own = corp.replies.get(sid, [])
        out["own_pool_size"] = len(own)
        raw, rx = _rx_of(anchor)
        out["predicate"] = raw
        if rx is None:
            out["status"] = "no_regex_predicate"
            out["detail"] = "reply_text 锚点没有正则谓词（只有存在性），本工具无从校准命中率"
            return out
        out["own_matches"] = sum(1 for t in own if rx.search(t))
    elif atype == "file_content":
        raw, rx = _rx_of(anchor)
        out["predicate"] = raw or (("field_path:%s" % anchor["field_path"])
                                   if anchor.get("field_path") else None)
        want = str(anchor.get("path") or "")
        fixture = {str(f.get("path")): str(f.get("content", ""))
                   for f in (sample.get("fixture") or [])}
        written = [f for f in corp.files.get(sid, []) if f["path"] == want]
        listed = want in corp.listing.get(sid, set())
        out["path_in_fixture"] = want in fixture
        out["path_written_in_run"] = len(written)
        out["path_in_workspace_listing"] = bool(listed) if corp.listing_available.get(sid) else None
        out["own_sample_all_paths"] = sorted({f["path"] for f in corp.files.get(sid, [])}
                                             | corp.listing.get(sid, set()) | set(fixture))
        # 内容池按证据强度逐层降级，每层都记出处（不许拿弱证据冒充强证据）
        if written:
            pool, out["content_provenance"] = [f["content"] for f in written], "real_file_write"
        elif want in fixture:
            pool, out["content_provenance"] = [fixture[want]], "paper_fixture（题面自带初始正文）"
        elif listed:
            pool, out["content_provenance"] = [], "workspace_listing_only（路径存在，正文不在证据里）"
        elif tasks:
            pool, out["content_provenance"] = [], None
            out["status"] = "path_never_produced"
            out["detail"] = ("该题在真实批里有 %d 个任务，但锚点钉的 %r 既不在真实工作区清单、"
                             "也没有任何 file_write、更不是题面 fixture；真实出现过的路径是 %s "
                             "⇒ 这条 hard+present 在真实行为下只能失败（与正则好坏无关，"
                             "属产物路径钉死的数据设计风险 / 或 Agent 确实没做）"
                             % (len(tasks), want, out["own_sample_all_paths"] or "（该题没有任何产物）"))
            return out
        else:
            pool, out["content_provenance"] = [], None
            out["status"] = "undecidable_no_matching_task"
            out["detail"] = "该题不在真实批里（run 覆盖 %d 个样本）⇒ 无法判它的产物谓词" % \
                            len(corp.task_of_sample)
            # 不早退：仍要走下面的全局可实现性计算，否则 file_content 与 memory/reply 口径不一致
            # （第一版这里早退，30 条 file_content 锚点的 realizable 全是 None）
        if not pool:
            if out["status"] is None:
                out["status"] = "undecidable_content_unavailable"
                out["detail"] = ("路径 %r 在真实工作区清单里存在，但正文不在证据中"
                                 "（listing 只有 sha/size）⇒ 判不了内容谓词，不当通过" % want)
            out["own_pool_size"] = 0
        else:
            out["own_pool_size"] = len(pool)
        fp = anchor.get("field_path")
        if fp:
            cand, unresolved = [], collections.Counter()
            for text in pool:
                ext = pathlib.Path(want).suffix.lower()
                if ext not in PARSEABLE:
                    unresolved["unsupported_format:" + (ext or "<none>")] += 1
                    continue
                try:
                    cand.append(scalarize(field_value(text, want, fp)))
                except EnvError:
                    unresolved["path_not_resolvable"] += 1
            out["own_pool_size"] = len(cand)
            out["field_path_unresolved"] = dict(unresolved)
            if rx is None:
                out["status"] = out["status"] or ("field_path_resolved" if cand
                                                  else "field_path_unresolvable")
                out["detail"] = out["detail"] or (
                    "field_path 在真实产物上解析出 %d 个值" % len(cand) if cand else
                    "field_path 在真实产物上一个都解析不出 ⇒ 运行时走铁律 3 记 env_error")
                return out
            out["own_matches"] = sum(1 for t in cand if rx.search(t))
        else:
            if rx is None:
                out["status"] = out["status"] or "no_regex_predicate"
                out["detail"] = out["detail"] or "file_content 只有存在性断言（无 content_regex/field_path）"
                return out
            out["own_matches"] = sum(1 for t in pool if rx.search(t))
        # 这里**不许早退**：还要走下面的全局可实现性计算，否则未跑过的题拿不到 realizable
        # （第一版在这早退，30 条 file_content 锚点的 realizable 全是 None）
    else:
        out["status"] = "unsupported_anchor_type"
        out["detail"] = "本工具不认这个锚点类型：%s" % atype
        return out

    # ② 可实现性：谓词在**全量真实语料**上能不能命中（这是红线4 要的"正则永不命中"判据）
    raw, rx = _rx_of(anchor)
    if rx is None and atype == "memory_entry":
        kr = anchor.get("key_regex")
        rx = compile_rx(str(kr)) if kr else None
        raw = str(kr) if kr else None
    if rx is not None:
        texts = corp.all_texts
        out["global_pool_size"] = len(texts)
        out["global_hits"] = sum(1 for t in texts if rx.search(t))
        out["realizable"] = out["global_hits"] > 0
        out["global_match_rate"] = (out["global_hits"] / len(texts)) if texts else None
    if atype == "memory_entry":
        # 可实现性 = 用**同一个真实匹配器** hit_entries 扫全量真实语料（回复/产物/记忆条目都当候选串）。
        # 第一版这里误用了"本样本自己的条目池"，于是 `花生`（真实回复里出现过）被判成不可实现——
        # 那不是死锚，那是本样本的记忆面没采到。冤案必须堵在口径里，不能靠人复核兜。
        try:
            global_entries = [{"key": t, "value": t} for t in corp.all_texts]
            out["global_pool_size"] = len(global_entries)
            out["global_hits"] = len(hit_entries(global_entries, anchor))
            out["realizable"] = out["global_hits"] > 0
            out["global_match_rate"] = (out["global_hits"] / len(global_entries)) \
                if global_entries else None
        except EnvError:
            pass

    if len(corp.all_texts) < min_pool:
        out["status"] = "undecidable_small_pool"
        out["detail"] = "全量真实语料只有 %d 条（< --min-pool %d）⇒ 判不了死活" % (
            len(corp.all_texts), min_pool)
        return out
    if out["status"] == "path_never_produced":
        return out                          # 更具体的发现优先，不被恒真判定盖掉
    gsize = out.get("global_pool_size") or 0
    rate = out.get("global_match_rate")
    if gsize and rate is not None and rate >= TAUTOLOGY_RATE:
        # 恒真是**谓词与全量真实语料**的性质，与该题跑过没跑过无关 ⇒ 必须在 no_matching_task 之前判。
        # 第一版把它放在早退之后，129 条结构锚点（.* / .+）一条都没标出来。
        out["status"] = "matches_everything"
        out["detail"] = ("在全量真实语料上命中 %d/%d（%.5f >= %.2f）：恒真谓词没有区分力"
                         "（结构锚点 .* / .+ 属预期设计，仍单列公示、不混进活锚计数）"
                         % (out["global_hits"], gsize, rate, TAUTOLOGY_RATE))
        return out
    if out["status"]:                      # undecidable_no_matching_task 已定
        out["detail"] += "；谓词可实现性（全量真实语料 %s 条命中 %d，命中率 %s）：%s" % (
            out.get("global_pool_size"), out["global_hits"],
            ("%.5f" % rate) if rate is not None else "n/a",
            "可实现" if out["realizable"] else "不可实现（**但本题没跑过，语料里没有它的值是预期的，"
                                              "不构成死锚指控**）")
        return out
    if not tasks:
        # 该题不在真实批里：memory/reply 类锚点也必须记不可判。第一版漏了这条，
        # 把 retain_pet_name_001（`芝麻`）报成死锚 —— 语料里没有它的值，因为这道题从没跑过。
        out["status"] = "undecidable_no_matching_task"
        out["detail"] = ("该题不在真实批里（run 覆盖 %d 个样本）⇒ 判不了证据到位性；"
                         "谓词在全量真实语料 %s 条上命中 %d 条（%s）"
                         % (len(corp.task_of_sample), out.get("global_pool_size"),
                            out["global_hits"], "可实现" if out["realizable"] else "不可实现"))
        return out

    if out["realizable"] is False:
        out["status"] = "suspect_dead_predicate" if expect == "present" else "unverified_negative"
        out["detail"] = ("正则 %r 在 %d 条真实语料上零命中" % (raw, out.get("global_pool_size", 0))) + (
            "：这条 present 判据在真实证据形状上永远满足不了（锚点误伤第一嫌疑）"
            if expect == "present" else
            "：这条 absent 判据从不触发，分不清『正确克制』与『死判据』")
        return out
    if out["own_matches"] == 0:
        if atype == "memory_entry" and tasks and not out.get("memory_probe_verified_for_this_sample"):
            out["status"] = "evidence_absent_probe_unverified"
            out["detail"] = ("谓词可实现，但该题记忆面观测里没命中；本样本的任务里有 Agent 的记忆探针"
                             "整轮未证实（%s）⇒ 读不到 ≠ 没记住，不指控锚点"
                             % ",".join(sorted(corp.probe_unverified)))
        elif atype == "reply_text" and out["own_pool_size"] == 0:
            out["status"] = "undecidable_no_reply_in_run"
            out["detail"] = "该题在真实批里没有 agent 回复行 ⇒ 判不了"
        else:
            out["status"] = "evidence_absent_not_predicate"
            out["detail"] = ("谓词在全量真实语料上命中 %d 条（可实现），但本样本自己的 %d 条候选里零命中"
                             " ⇒ 是证据/行为问题，不是正则死锚"
                             % (out["global_hits"], out["own_pool_size"]))
        return out
    # 恒真判定已在上面（早退之前）做完：它只看谓词与全量真实语料，不看本样本池。
    # 曾经放在这里，于是"本样本池只有 1 条"时任何命中的锚点都被冤枉成恒真（守卫抓到过 3 例）。
    out["status"] = "live" if expect == "present" else "live_negative"
    out["detail"] = "本样本 %d/%d 命中；全量语料命中 %d" % (
        out["own_matches"], out["own_pool_size"], out["global_hits"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper", default="build/paper-r1.jsonl")
    ap.add_argument("--run-dir", dest="run_dir", default="runs/run_v02_first",
                    help="真实证据 run（预注册 §3.4 指定 run_v02_first）")
    ap.add_argument("--out", default=None,
                    help="凭据输出（默认 notes/anchorcal-<卷指纹前12位>.json）")
    ap.add_argument("--min-pool", dest="min_pool", type=int, default=20,
                    help="全量真实语料低于此数 ⇒ 一律 undecidable_small_pool，不判通过")
    ap.add_argument("--check", action="store_true", help="与已存在凭据比对（漂移退出 2）")
    a = ap.parse_args(argv)

    paper = ROOT / a.paper
    run_dir = ROOT / a.run_dir
    if not paper.is_file():
        print("缺 %s：先跑 build_paper —— 不猜读数" % a.paper, file=sys.stderr)
        return 2
    if not (run_dir / "evidence.jsonl").is_file():
        print("缺 %s/evidence.jsonl：红线4 要真实证据形状，不用合成语料顶替" % a.run_dir,
              file=sys.stderr)
        return 2
    rows = load_paper(paper)
    idx = EvidenceIndex.load(run_dir)
    corp = Corpus(idx)

    per_anchor, hist = [], collections.Counter()
    for s in rows:
        for anchor in s.get("anchors") or []:
            r = calibrate(anchor, s, corp, a.min_pool)
            r = {"sample_id": s.get("id"), "dimension": s.get("dimension"),
                 "anchor_id": anchor.get("id"), "desc": anchor.get("desc"), **r}
            hist[r["status"]] += 1
            per_anchor.append(r)

    def pick(status):
        return [{"sample_id": r["sample_id"], "anchor_id": r["anchor_id"],
                 "dimension": r["dimension"], "predicate": r["predicate"],
                 "path": r["path"], "detail": r["detail"]}
                for r in per_anchor if r["status"] == status]

    doc = {
        "format": FORMAT,
        "paper": a.paper, "paper_sha256": sha(paper),
        "run_dir": a.run_dir, "run_id": idx.manifest.get("run_id"),
        "run_suite_version": idx.manifest.get("suite_version"),
        "evidence_sha256": sha(run_dir / "evidence.jsonl"),
        "min_pool": a.min_pool,
        "tautology_rate": TAUTOLOGY_RATE,
        "scan_scope": corp.scan_scope(),
        "status_histogram": dict(hist),
        "anchors_total": len(per_anchor),
        "suspect_dead_predicate": pick("suspect_dead_predicate"),
        "path_never_produced": pick("path_never_produced"),
        "unverified_negative": pick("unverified_negative"),
        "evidence_absent_probe_unverified": pick("evidence_absent_probe_unverified"),
        "evidence_absent_not_predicate": pick("evidence_absent_not_predicate"),
        "matches_everything": sum(1 for r in per_anchor if r["status"] == "matches_everything"),
        # §3.4 字面要的"零正则永不命中"：在全量真实语料上零命中的谓词，按该题跑过没跑过分两类。
        # 跑过 ⇒ 是死锚指控（语料与题目同域，零命中说不过去）；
        # 没跑过 ⇒ 只是弱信号（语料里本来就没有这道题自己的值，零命中是预期的，不构成指控）。
        "unrealizable_in_global_corpus": {
            "global_pool_size": corp.scan_scope()["global_candidate_strings"],
            "covered_by_run": [r for r in pick("suspect_dead_predicate")],
            "not_covered_by_run": [
                {"sample_id": r["sample_id"], "anchor_id": r["anchor_id"],
                 "dimension": r["dimension"], "type": r["type"], "expect": r["expect"],
                 "predicate": r["predicate"]}
                for r in per_anchor
                if r["realizable"] is False and r["status"] != "suspect_dead_predicate"],
            "note": "not_covered_by_run 里的零命中是预期的（该题从未跑过，真实语料里没有它自己的值）；"
                    "要判它们死活必须先有覆盖这些题的真实批 —— 这正是红线4 在 600 之前关不掉的原因"},
        "undecidable_total": sum(v for k, v in hist.items() if k.startswith("undecidable")),
        "samples_total": len(rows),
        "samples_covered_by_run": sum(1 for s2 in rows if s2.get("id") in corp.task_of_sample),
        "decidable_anchors": sum(1 for r in per_anchor if not r["status"].startswith("undecidable")
                                 and r["status"] not in ("not_calibratable_event_anchor",)),
        "per_anchor": per_anchor,
        "limits": [
            "校准语料来自 suite_version=%s 的真实批（run_id=%s，%d 个样本 / %d 个任务），"
            "本卷 100 题里只有 %d 题在其中 ⇒ 其余题只能量『谓词可实现性』，量不到『证据到位性』"
            % (idx.manifest.get("suite_version"), idx.manifest.get("run_id"),
               len(corp.task_of_sample), len(corp.tasks),
               sum(1 for s in rows if s.get("id") in corp.task_of_sample)),
            "退出码 0 只表示校准跑完；红线4 的判据是 suspect_dead_predicate == 0，读数看凭据",
            "matches_everything 不等于合格：恒真谓词没有区分力；结构锚点属预期但一律单列公示",
            "undecidable_* / evidence_absent_* / not_calibratable_* 都不折算成通过"
            "（CONTRACT §5：采不到 ≠ 没发生），也不指控锚点 —— 它们是采集与题目设计的缺口，"
            "分别归 attribution / 红线3 / F1 人审",
            "hermes 的记忆探针在本 run 整轮未证实（mem_probe_unverified）⇒ 记忆类锚点的"
            "『证据到位性』读数对它一律不可判，这是采集能力缺口不是锚点缺陷",
        ],
    }
    out = ROOT / (a.out or ("notes/anchorcal-%s.json" % doc["paper_sha256"][:12]))
    if a.check:
        if not out.is_file():
            print("凭据不存在：%s" % out.relative_to(ROOT), file=sys.stderr)
            return 2
        old = json.loads(out.read_text(encoding="utf-8"))
        old.pop("generated_at", None)
        cur = dict(doc); cur.pop("generated_at", None)
        if json.dumps(old, sort_keys=True) != json.dumps(cur, sort_keys=True):
            print("anchorcal 凭据与重算不一致 —— 重跑 okb anchorcal", file=sys.stderr)
            return 2
        print("凭据与重算一致 ✓（死锚嫌疑 %d / 路径从未落盘 %d / 未验证负判据 %d / 恒真 %d / 不可判 %d，共 %d 条锚点）"
              % (len(doc["suspect_dead_predicate"]), len(doc["path_never_produced"]),
                 len(doc["unverified_negative"]), doc["matches_everything"],
                 doc["undecidable_total"], len(per_anchor)))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("已写 %s" % out.relative_to(ROOT))
    sc = doc["scan_scope"]
    print("锚点 %d 条 | 真实语料：回复 %d / 记忆条目 %d / 产物 %d（全局候选串 %d）| run 覆盖 %d 个样本"
          % (len(per_anchor), sc["agent_reply_texts"], sc["memory_entries"], sc["file_writes"],
             sc["global_candidate_strings"], sc["samples_with_evidence"]))
    for k, v in sorted(hist.items(), key=lambda kv: -kv[1]):
        print("  %-36s %d" % (k, v))
    for key in ("suspect_dead_predicate", "path_never_produced"):
        if doc[key]:
            print("%s：" % key)
            for r in doc[key][:15]:
                print("  %s:%s [%s] %s" % (r["sample_id"], r["anchor_id"], r["dimension"],
                                           (r["predicate"] or r["path"] or "")[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
