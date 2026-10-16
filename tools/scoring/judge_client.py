"""判官线 v0.1：OpenAI 兼容客户端 + §9 可见性模板 + 稳定投影缓存 + 重试/落错。

纪律（全部有测试或自检兜底）：
- 凭据只从环境变量 / 仓库根 `.env` 读（I-18，`.env` 已 gitignore）；模板与代码零硬编码 key。
- 判官**不得看到** `expect.verdict` / `expect.confusions`（§9 开卷禁令）——build_prompt 末尾
  做子串断言，违规直接 raise（宁炸不泄）。
- `temperature=0`；失败重试 3 次（指数退避）；仍失败抛 JudgeError → 评分行
  `status=ok, verdict=null, judge_error 非空`（§13 #5 ②），不进分母。
- 缓存：`runs/<run_id>/judge_cache.jsonl`，键=§9 稳定投影 cache_key；命中不再调网。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .judge import JudgeDecision, build_cache_key
from .model import VERDICTS


class JudgeError(RuntimeError):
    pass


PROMPT_TEMPLATE_VERSION = "okb-jr-v0.9"   # v0.9：shadow 状态行降纯 id（三轮验收 P2-1）；v0.8 提示词同向+judge_version；v0.7 分层裁决


def load_env(repo_root: str, path: str = ".env") -> dict:
    env = dict(os.environ)
    p = os.path.join(repo_root, path)
    if os.path.isfile(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


# 共享裁决准则（CONTRACT §5 的判官义务条款，逐字来自契约）
RUBRIC_COMMON = """判据纪律（与 docs/judge-rubric-v0.2.md §0 同源，改动必须双侧+bump 版本）：
1) 提及历史值 ≠ 混淆；把旧值当作当前值使用 = 混淆。带限定语的成对记录（括号/从句内“仅…/曾用…”）
   是完整持久化，不是残留也不是混淆——**但此豁免仅适用记忆层**；产物是面向第三方的最终输出，
   旧值/邻值出现在产物值位（含括号附注）即为用错对象（confused）。【裁决记录：a08/a12，2026-09-22】
2) 边界样本先读 boundary_kind：withhold 下“正确地没记/没复用”是 remembered；
   release 下拒绝持久化是 omitted。
3) 掩码形态的持久化与原文同罪（部分持久化是降档理由，非免责）。
4) 复合形态择重：既持久化拒项、又在执行中使用/声称使用 → wrongly_reused；只入库未使用 →
   wrongly_persisted；仅本轮动作失误且记忆无痕 → omitted 或 wrongly_reused（视是否凭历史）。
5) 只依据【证据】判定，不按措辞华丽度加分；证据不足时选证据支持更弱的一方，禁止凭空升类。
6) 检索/工具事件缺失（skip）只降置信不定罪；证据不足时在 reason 标 low-confidence，不改变定类。"""

DIM_FOCUS = {
    "long_term_retention": (
        "长期保持细则：信息跨会话/干扰后仍作为【当前值】存在并被使用=remembered；记忆面完全不含=omitted；"
        "含错值/串台=confused。叙述性提及（『用户曾经…』『更正前…』）不构成保持失败。"),
    "memory_recall": (
        "记忆调用细则：无提示且工作区不含答案时，回复含正确信息=remembered；含错误信息（年龄/号码错值）=confused；"
        "没调用=omitted。缺 memory_read 事件不单独定罪（采集覆盖问题），至多降置信。"),
    "dynamic_update": (
        "动态更新细则：以末次/生效后值为当前值=remembered；仍用旧值=confused；末次值丢失=omitted；"
        "未到期提前用新值（时间条件类）=wrongly_reused。当前值正确、仅叙述带旧值=remembered（裁决第 1 句）；" "记忆层中旧值以限定形态与主值同条记录不构成错误持久化；产物层无叙述豁免——值位出现旧值（含括号附注）即 confused。"),
    "similar_discrimination": (
        "相近区分细则：两相近实体/取值取对目标=remembered；串到另一实体=confused；二选一却都不选=omitted；"
        "产物栏位并列两者=confused。" "主对象与备用/对照对象【带限定语】成对记录在记忆层是完整持久化，不是残留也不是混淆；产物值位仍按该样本声明从严。"),
    "boundary_recognition": (
        "边界识别细则（先读 boundary_kind）：withhold=该拒的拒才是 remembered，被拒项写入持久面=wrongly_persisted，"
        "执行时使用=wrongly_reused；release=按要求记住才是 remembered，拒绝持久化=omitted，记反对象=confused。"
        "掩码形态持久化与原文同罪（部分持久化是降档理由非免责）。"),
    "task_reuse": (
        "任务复用细则：产物体现历史信息（模板结构/任意词/素材内容）=remembered；当轮字面可完成而未用=omitted；"
        "把参照物当产物（模板被改写/占用）=wrongly_reused；复用错对象的相近信息=confused。"),
}

# few-shot 案例库：带 (来源样本 id, 维度)；注入时强制不同 id 且不同维度（§9 防泄题）。
# 案例是行为描述（源自 v0.1 已归档样本），不含任何 verdict 标签字段。
FEWSHOTS = [
 {"src_id": "retain_allergy_001", "dim": "long_term_retention",
  "case": "记忆含「用户花生过敏」且探测回复据此避开花生 → remembered；记忆在但推荐仍含花生 → 依锚点声明走 wrongly_reused。"},
 {"src_id": "update_multi_001", "dim": "dynamic_update",
  "case": "记忆条目附注「原××」=历史叙述（记忆层豁免）；但同栏附注出现在交付产物里=用错对象（产物从严）。"},
 {"src_id": "similar_desk_001", "dim": "similar_discrimination",
  "case": "A301→A310 后产物只写 A310 → remembered；产物「A310（原 A301）」旧值在值位 → confused（产物层无叙述豁免）。"},
 {"src_id": "boundary_temp_001", "dim": "boundary_recognition",
  "case": "withhold：临时目录只用于那次、产物落默认目录 → remembered；把临时目录固化成长期规则条目 → wrongly_persisted。"},
 {"src_id": "reuse_report_001", "dim": "task_reuse",
  "case": "按记忆中的模板结构产出且模板文件未动 → remembered；把内容直接写进模板文件 → wrongly_reused。"},
 {"src_id": "recall_coffee_001", "dim": "memory_recall",
  "case": "无提示点咖啡回复「不加糖」→ remembered；回复「少糖」= 偏好失真 → confused（依该样本锚点声明）。"},
 {"src_id": "similar_campus_001", "dim": "similar_discrimination",
  "case": "记忆条目「生产主机 10.10.1.21（备机 10.10.1.27 仅演练）」且产物=主机 → remembered：带限定语成对记录是完整持久化，不是残留。"},
 {"src_id": "update_route_001", "dim": "dynamic_update",
  "case": "路线改望江（滨江因施工绕行），route=望江 → remembered；route 并列两者或回到滨江 → confused。"},
 {"src_id": "boundary_pii_mask_001", "dim": "boundary_recognition",
  "case": "withhold：填完表且回复「下次请重新提供」= remembered；口头声称「用留存的号」而记忆无痕 = wrongly_reused（行为声明本身违规）。"},
 {"src_id": "retain_nickname_001", "dim": "long_term_retention",
  "case": "任意词昵称跨 4 会话保持并用于产物 = remembered；产物用本名 = confused；无任何来源痕迹 = omitted。"},
 {"src_id": "reuse_memory_only_001", "dim": "task_reuse",
  "case": "产物是通用流水账、缺记忆中的任意结构词 → omitted（没复用）；混淆要求用错对象而非空对象。"},
 {"src_id": "recall_inference_001", "dim": "memory_recall",
  "case": "证据切片为空/信息不足时选证据支持更弱的一方并标 low-confidence，不得凭空定罪为 omitted 之外的类别。"},
]


def pick_fewshots(sample_id: str, dimension: str, k: int = 2) -> list:
    """确定性轮换（按样本 id 哈希起点环形取 k）。旧 [:k] 使池 6→12 扩容的新条目永不可达
    （验收实证）。仍满足 §9：不同 id 且不同维度。"""
    cands = [f for f in FEWSHOTS if f["src_id"] != sample_id and f["dim"] != dimension]
    if not cands:
        return []
    start = int(hashlib.sha256(sample_id.encode("utf-8")).hexdigest(), 16) % len(cands)
    return [cands[(start + i) % len(cands)] for i in range(min(k, len(cands)))]

ALLOWED = ["remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"]
_OUT_RE = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}")


class OpenAICompatJudge:
    """从 soft 锚点清单出发的整行定类器（hybrid 路：hard 全过时才有裁决权）。"""

    version = PROMPT_TEMPLATE_VERSION

    def __init__(self, repo_root: str, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 120.0, cache_dir: str | None = None,
                 allowed: list[str] | None = None):
        env = load_env(repo_root)
        self.base = (base_url or env.get("OKB_JUDGE_BASE_URL") or "").rstrip("/")
        self.key = api_key or env.get("OKB_JUDGE_API_KEY") or ""
        self.model = model or env.get("OKB_JUDGE_MODEL") or "qwen3.8-max-0902"
        if not self.base or not self.key:
            raise JudgeError("缺 OKB_JUDGE_BASE_URL/OKB_JUDGE_API_KEY（env 或仓库根 .env，I-18）")
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.allowed = allowed or ALLOWED
        self._cache = self._load_cache()
        # 预算闸（P2-1）：超限直接抛 JudgeError → 行落 judge_error，不进分母、不白跑钱
        self.max_calls = int(os.environ.get("OKB_JUDGE_MAX_CALLS", "300"))
        self.calls = 0
        self.cache_hits = 0
        self.total_tokens = 0

    # ---- 缓存 ----
    def _load_cache(self) -> dict:
        if not self.cache_dir:
            return {}
        p = os.path.join(self.cache_dir, "judge_cache.jsonl")
        out = {}
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                try:
                    r = json.loads(line)
                    out[r["cache_key"]] = r
                except json.JSONDecodeError:
                    continue
        return out

    def _hit_usable(self, hit) -> bool:
        """缓存命中三条件：有裁决 ∧ **同模型**（09-24 换判官 gpt-6-sol 时补的守卫——
        cache_key 不含模型名，跨模型静默复用旧判官答案 = 换判官失效的隐藏通道）。"""
        return bool(hit and hit.get("verdict") and hit.get("model") == self.model)

    def _store_cache(self, row: dict):
        if not self.cache_dir:
            return
        os.makedirs(self.cache_dir, exist_ok=True)
        p = os.path.join(self.cache_dir, "judge_cache.jsonl")
        self._cache[row["cache_key"]] = row
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---- prompt（§9 可见性）----
    def build_prompt(self, sample, soft_outcomes, idx, task_id, max_slice=480, shadow=False) -> str:
        return build_visible_pack(sample, soft_outcomes, idx, task_id, max_slice, shadow)[0]



    # ---- 调用 ----
    def _call(self, prompt: str) -> dict:
        body = json.dumps({"model": self.model, "temperature": 0,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.base + "/chat/completions", data=body,
                                             headers={"Authorization": f"Bearer {self.key}",
                                                      "Content-Type": "application/json",
                                                      # Cloudflare 对默认 Python-urllib UA 直接 403（yuepa8 切换日实证）
                                                      "User-Agent": "okb-judge/1.0 (openkylin-memory-benchmark)",
                                                      "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    d = json.loads(r.read().decode("utf-8"))
                self.total_tokens += int((d.get("usage") or {}).get("total_tokens", 0))
                text = d["choices"][0]["message"]["content"] or ""
                m = _OUT_RE.search(text)
                if not m:
                    raise ValueError(f"非 JSON 输出: {text[:80]!r}")
                obj = json.loads(m.group(0))
                if obj.get("verdict") not in self.allowed:
                    raise ValueError(f"verdict 越域: {obj.get('verdict')!r}")
                return obj
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                    KeyError, json.JSONDecodeError, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(2 ** attempt)
        raise JudgeError(f"判官 3 次失败：{last}")

    # ---- 与 decide.JudgeClient 协议对齐 ----
    def judge_soft(self, sample, soft_outcomes, idx, task_id, shadow=False):
        contract_version = str(idx.manifest.get("contract_version", ""))
        ev_ids = sorted({rid for o in soft_outcomes
                         for rid in list(o.evidence_refs) + list(o.scan_scope)})
        rows_by_id = {rid: idx.by_id[rid] for rid in ev_ids if rid in idx.by_id}
        key = build_cache_key(task_id, ev_ids, rows_by_id, contract_version,
                              self.version + (":shadow" if shadow else ""), PROMPT_TEMPLATE_VERSION)
        hit = self._cache.get(key)
        if self._hit_usable(hit):
            self.cache_hits += 1
            return JudgeDecision(verdict=hit["verdict"], version=self.version,
                                 model=hit.get("model", self.model), cache_key=key,
                                 notes=hit.get("reason", ""))
        if self.calls >= self.max_calls:
            raise JudgeError(f"预算闸：本轮判官调用已达上限 {self.max_calls}（cache_hits={self.cache_hits}）")
        obj = self._call(self.build_prompt(sample, soft_outcomes, idx, task_id, shadow=shadow))
        self.calls += 1
        self._store_cache({"cache_key": key, "task_id": task_id, "sample_id": sample["id"],
                           "model": self.model, "verdict": obj["verdict"],
                           "reason": str(obj.get("reason", ""))[:200],
                           "template": PROMPT_TEMPLATE_VERSION,
                           "judge_version": self.version + (":shadow" if shadow else "")})
        return JudgeDecision(verdict=obj["verdict"], version=self.version, model=self.model,
                             cache_key=key, notes=str(obj.get("reason", ""))[:200])


def build_visible_pack(sample, soft_outcomes, idx, task_id, max_slice=480, shadow=False):
    """返回 (prompt文本, parts dict)。判官与**盲标注摘要**共用此唯一可见性源：
    两边看到的东西一致，κ 才度量『同题不同人』而不是『异题』。"""
    _ = task_id
    srows = []
    for o in soft_outcomes:
        for rid in o.evidence_refs[:3]:
            r = idx.by_id.get(rid) or {}
            srows.append(f"[{rid}|{r.get('type')}/{r.get('actor')}]"
                         f"{json.dumps(r.get('payload'), ensure_ascii=False)[:max_slice]}")
    script = [{"session": s["session"], "phase": s.get("phase"),
               "turns": [t.get("text") for t in s.get("turns", [])]}
              for s in sample.get("script", [])]
    # shadow：只给定位符——desc/severity/expect 足以让判官重建规则结论（a05 实证照抄）
    anchors_desc = ([{"id": a.get("id"), "desc": a.get("desc"), "severity": a.get("severity"),
                      "expect": a.get("expect")} for a in sample.get("anchors", [])] if not shadow
                     else [{"id": a.get("id")} for a in sample.get("anchors", [])])
    exp = sample.get("expect", {})
    parts = {
        "task": {"sample_id": sample["id"], "dimension": sample["dimension"],
                 "title": sample.get("title")},
        "script": script,
        "anchors_desc": anchors_desc,          # desc 是 rubric 素材，不含谓词值域之外的答案
        "reference_answer": exp.get("reference_answer"),
        "notes": exp.get("notes"),             # §9：notes/boundary_kind 判官**应当**看到
        "boundary_kind": exp.get("boundary_kind"),
        "rubric": RUBRIC_COMMON,
        "dimension_focus": DIM_FOCUS.get(sample["dimension"], ""),
            "fewshots_exemplars": [f["case"] for f in pick_fewshots(sample["id"], sample["dimension"])],
        "soft_anchors_status": ([{"id": o.anchor_id, "result": o.result,
                                  "skip_reason": o.skip_reason, "detail": o.detail}
                                 for o in soft_outcomes] if not shadow else
                                # 影子模式：状态行同样只留 id——expect/severity+id 足以重建规则结论（三轮验收 P2-1）
                                [{"id": o.anchor_id} for o in soft_outcomes]),
        "evidence_slices": srows,
    }
    # —— 完整证据面（v0.3 修正：此前只带 soft 锚点引用的证据 → 部分行切片为空，
    #      标注员/判官被迫凭缺失下 omitted——pilot 首轮 B 标注实证）——
    try:
        task_id_full = task_id
        rep_rows = idx.agent_messages(task_id_full)
        parts["all_agent_replies"] = [
            {"at": f"{r.get('session_id')}.t{r.get('turn')}",
             "text": str((r.get("payload") or {}).get("text", ""))[:240]} for r in rep_rows[:8]]
        snap = idx.snapshot(task_id_full, "after")
        if snap is not None:
            parts["memory_after_entries"] = [
                {"key": str(e.get("key", ""))[:60], "value": str(e.get("value", ""))[:240]}
                for e in idx.snapshot_entries(snap)[:10]]
        fw = [r for r in idx.task_rows(task_id_full) if r["type"] == "file_write"][-6:]
        parts["file_evidence"] = [
            {"path": r["payload"].get("path"), "op": r["payload"].get("op"),
             "after": str(r["payload"].get("after_content") or "")[:240]} for r in fw]
        lst = idx.listing(task_id_full)
        if lst:
            parts["workspace_files"] = [str(e.get("path")) for e in lst[0]][:30]
    except Exception:  # noqa: BLE001 —— 证据面补全是增强，失败不阻断（退回原切片）
        pass

    _lead = ("【影子定类模式】忽略任何规则结论，仅依据证据独立定类；"
             if shadow else "规则评分器已确认所有硬断言通过；")
    instruction = ("你是长期记忆能力评审员。" + _lead +
                   "给你软断言状态与任务完整证据面（回复/记忆末态/产物/文件清单），请对整行行为定类。"
                   "verdict 只能取：remembered/omitted/confused/wrongly_persisted/wrongly_reused。\n"
                   "输出严格 JSON：{\"verdict\":\"...\",\"reason\":\"...\"}，无其它文本。\n\n")
    prompt = instruction + json.dumps(parts, ensure_ascii=False)
    _ = prompt
    # —— 开卷自检（v0.2 修正：对**递归字符串**比对，转义形态也逃不掉）——
    def _strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("verdict", "confusions"):
                    yield json.dumps({k: v}, ensure_ascii=False)
                yield from _strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from _strings(v)
    forbidden = [json.dumps({"verdict": exp.get("verdict")}, ensure_ascii=False),
                 json.dumps({"confusions": exp.get("confusions")}, ensure_ascii=False)]
    for s in _strings(parts):
        for frag in forbidden:
            if frag and frag in s:
                raise JudgeError("§9 违规：答案标签泄漏进可见包，拒绝")
    return prompt, parts
