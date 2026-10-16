"""okb 数据契约 · 校验器（两层）

第一层 schema  ：字段名 / 类型 / 枚举 / 模式（schemas/sample.schema.json）
第二层 loader  ：schema 表达不了的跨字段约束（docs/CONTRACT.md §5）

为什么必须有第二层：纯 schema 校验放过 11 类语义坏样本——
例如「同一个 session 出现两次」「phase 顺序颠倒」「anchors.turn 指向不存在的轮次」
「rule_only 却含 soft 锚点」，它们全部能通过 schema，但会让引擎跑错或让分数失真。

用法：
    python tools/validate.py schemas/example-samples.yaml
    python tools/validate.py samples/*.yaml --json
    python tools/validate.py paper.jsonl --quiet
退出码：0 = 无 error；1 = 有 error；2 = 用法/读取失败
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from loader import load_any  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "schemas" / "sample.schema.json"

VERDICTS = {"remembered", "omitted", "confused", "wrongly_persisted", "wrongly_reused"}
PHASE_ORDER = {"write": 0, "disturb": 1, "update": 2, "probe": 3}
PLACEHOLDER_RE = re.compile(r"<PII_[A-Z0-9_]+>")                 # 规范写法（区分大小写）
LOOSE_PLACEHOLDER_RE = re.compile(r"<PII[^>]*>?", re.IGNORECASE)  # 宽松探测：抓拼写变体
TURN_RE = re.compile(r"^s(\d+)\.t(\d+)$")
REGEX_FIELDS = ("value_regex", "key_regex", "content_regex", "regex", "arg_regex")


@dataclass
class Issue:
    code: str
    level: str  # error | warning
    message: str
    sample_id: str = "-"
    locator: str = ""  # 例如 anchors[2].turn

    def render(self, source: str) -> str:
        loc = f":{self.locator}" if self.locator else ""
        return f"{self.level.upper():7} [{self.code}] {source}:{self.sample_id}{loc}: {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    def add(self, code: str, level: str, message: str, sample_id: str = "-", locator: str = "") -> None:
        self.issues.append(Issue(code, level, message, sample_id, locator))


# ── 第一层：schema ──────────────────────────────────────────────────────────

def schema_validate(sample: dict, report: Report, schema: dict) -> None:
    from jsonschema import Draft7Validator

    sid = sample.get("id", "-")
    for err in sorted(Draft7Validator(schema).iter_errors(sample), key=lambda e: list(e.path)):
        loc = ".".join(str(p) for p in err.path) or "(root)"
        report.add("E001", "error", f"schema 校验失败 @ {loc}: {err.message}", sid, loc)


# ── 第二层：loader ──────────────────────────────────────────────────────────

def _iter_texts(sample: dict) -> Iterable[tuple[str, str]]:
    for si, sess in enumerate(sample.get("script") or []):
        if not isinstance(sess, dict):
            continue
        for ti, turn in enumerate(sess.get("turns") or []):
            if not isinstance(turn, dict):
                continue
            yield f"script[{si}].turns[{ti}].text", turn.get("text") or ""
    for fi, fx in enumerate(sample.get("fixture") or []):
        if not isinstance(fx, dict):
            continue
        yield f"fixture[{fi}].content", fx.get("content") or ""
        yield f"fixture[{fi}].path", fx.get("path") or ""


def _check_script(sample: dict, report: Report, sid: str) -> dict[str, dict]:
    """返回 session_id -> {turn_no: True} 的可解析索引。"""
    index: dict[str, dict[int, bool]] = {}
    session_ids: list[str] = []
    phases: list[str] = []
    dup_found = False
    for si, sess in enumerate(sample.get("script") or []):
        if not isinstance(sess, dict):
            report.add("E001", "error", f"script[{si}] 必须是对象（会话），实际是 {type(sess).__name__}", sid, f"script[{si}]")
            continue
        sess_id = sess.get("session")
        if not isinstance(sess_id, str):
            continue
        if sess_id in index:
            dup_found = True
            report.add("E101", "error", f"会话 {sess_id} 出现了两次；一个数组元素 = 一个会话，session 必须唯一", sid, f"script[{si}].session")
        session_ids.append(sess_id)
        phases.append(sess.get("phase") or "")
        index[sess_id] = {ti + 1: True for ti in range(len(sess.get("turns") or []))}

    # E102 编号连续（重复会话已报 E101 时跳过，避免级联报错干扰作者）
    if not dup_found:
        for expect_no, sess_id in enumerate(session_ids, start=1):
            m = re.match(r"^s(\d+)$", sess_id)
            if not m:
                continue
            if int(m.group(1)) != expect_no:
                report.add("E102", "error", f"会话编号不连续：第 {expect_no} 个会话应为 s{expect_no}，实际是 {sess_id}", sid, "script")
                break

    # E103 phase 非递减
    ranks = [PHASE_ORDER.get(p, -1) for p in phases]
    for i in range(1, len(ranks)):
        if ranks[i] < ranks[i - 1]:
            report.add("E103", "error",
                       f"phase 顺序非法：{session_ids[i-1]}({phases[i-1]}) → {session_ids[i]}({phases[i]})；必须按 write < disturb < update < probe 非递减",
                       sid, f"script[{i}].phase")
    return index


def _check_anchors(sample: dict, report: Report, sid: str, index: dict[str, dict]) -> None:
    anchors = sample.get("anchors") or []
    seen_ids: set[str] = set()
    has_hard_positive = False

    for ai, a in enumerate(anchors):
        if not isinstance(a, dict):
            report.add("E001", "error", f"anchors[{ai}] 必须是对象，实际是 {type(a).__name__}", sid, f"anchors[{ai}]")
            continue
        loc = f"anchors[{ai}]"
        aid = a.get("id")

        # E108 锚点 id 唯一
        if aid in seen_ids:
            report.add("E108", "error", f"锚点 id 重复：{aid}（会让 verdicts.evidence_refs 指向不明）", sid, loc)
        seen_ids.add(aid)

        if a.get("severity") == "hard" and a.get("expect") == "present":
            has_hard_positive = True

        # E106/E107 turn 可解析 + 只能指向 probe 会话
        turn = a.get("turn")
        if isinstance(turn, str) and turn != "*":
            m = TURN_RE.match(turn)
            if not m:
                report.add("E106", "error", f"turn 格式非法：{turn}（应为 s3.t1）", sid, loc + ".turn")
            else:
                sess_id = f"s{m.group(1)}"
                turn_no = int(m.group(2))
                if sess_id not in index:
                    report.add("E106", "error", f"turn 指向不存在的会话：{turn}", sid, loc + ".turn")
                elif turn_no not in index[sess_id]:
                    report.add("E106", "error", f"turn 指向不存在的轮次：{turn}（{sess_id} 只有 {len(index[sess_id])} 轮）", sid, loc + ".turn")
                else:
                    phases = {s.get("session"): s.get("phase") for s in sample.get("script") or []}
                    if phases.get(sess_id) != "probe":
                        report.add("E107", "error",
                                   f"turn 指向非 probe 会话：{turn}（phase={phases.get(sess_id)}）；只有 probe 会话允许被锚点引用",
                                   sid, loc + ".turn")

        # E110 正则可编译（value_regex 不允许写 "*"——那不是一个可求值的正则）
        for f in REGEX_FIELDS:
            v = a.get(f)
            if not isinstance(v, str):
                continue
            if f == "value_regex" and v == "*":
                report.add("E110", "error", f"{loc}.value_regex 不能为 \"*\"：它不是可求值的正则（会让这条锚点彻底不生效）；全量扫描请用 key: \"*\" + 具体 value_regex", sid, f"{loc}.{f}")
                continue
            try:
                re.compile(v)
            except re.error as exc:
                report.add("E110", "error", f"{loc}.{f} 正则不可编译：{exc}", sid, f"{loc}.{f}")

        # E116 key="*" 必须配 value_regex（schema 已查，此处兜底并给更清楚的说明）
        if a.get("key") == "*" and not a.get("value_regex"):
            report.add("E116", "error", f"{loc}：key=\"*\" 表示全量扫描，必须同时给 value_regex，否则断言无意义", sid, loc)

        # W202 hard + absent 未显式写判定时点
        if a.get("severity") == "hard" and a.get("expect") == "absent" and "evaluate_at" not in a:
            report.add("W202", "warning", f"{loc}：hard+absent 锚点未显式写 evaluate_at（默认 snapshot_after，建议写明以免误判）", sid, loc)

        # W203 must_differ_from_fixture / must_equal_fixture 但 fixture 里没有该文件
        if a.get("path"):
            fx_paths = {fx.get("path") for fx in sample.get("fixture") or [] if isinstance(fx, dict)}
            if a["path"] not in fx_paths:
                if a.get("must_differ_from_fixture"):
                    report.add("W203", "warning", f"{loc}：must_differ_from_fixture=true 但 fixture 中没有 {a['path']}（该文件是新建的，此标记多余）", sid, loc)
                if a.get("must_equal_fixture"):
                    report.add("W203", "warning", f"{loc}：must_equal_fixture=true 但 fixture 中没有 {a['path']}（无参照物可对比，此标记无法生效）", sid, loc)

        # E118 must_equal_fixture 与 expect=absent 互斥；也不能同时要求“改了”和“没改”
        if a.get("must_equal_fixture"):
            if a.get("expect") == "absent":
                report.add("E118", "error", f"{loc}：must_equal_fixture 与 expect=absent 互斥（“不存在”的文件无法与 fixture 比对）", sid, loc)
            if a.get("must_differ_from_fixture"):
                report.add("E118", "error", f"{loc}：must_equal_fixture 与 must_differ_from_fixture 不能同时为 true", sid, loc)

    # E104 正向门槛
    if not has_hard_positive:
        report.add("E104", "error",
                   "缺少 hard + present 的正向锚点：Agent 崩溃或空转也能靠负证据拿满分（无证据的通过）",
                   sid, "anchors")

    # E105 rule_only 必须全 hard
    if sample.get("score_policy") == "rule_only":
        soft = [a.get("id") for a in anchors if a.get("severity") == "soft"]
        if soft:
            report.add("E105", "error", f"score_policy=rule_only 但存在 soft 锚点 {soft}；规则路判不了语义项，请改 hybrid 或把锚点升为 hard", sid, "score_policy")

    # W204 judge_only 下不该把 hard 锚点当裁决依据。
    # 注意：E104 强制每个样本都要有一条 hard+present 正向门槛，所以不能对“所有 hard”报警——
    # 只对带裁决语气的 hard+absent 报警，否则 judge_only 整类样本永远无法通过 --strict。
    if sample.get("score_policy") == "judge_only":
        hard_absent = [a.get("id") for a in anchors if isinstance(a, dict) and a.get("severity") == "hard" and a.get("expect") == "absent"]
        if hard_absent:
            report.add("W204", "warning", f"score_policy=judge_only 但存在 hard+absent 锚点 {hard_absent}；hard 只应作证据、不作裁决，建议改 hybrid 或降为 soft", sid, "score_policy")


def _reply_literal_tokens(pattern: str) -> set[str]:
    """Conservative W212 lexer, not a general regex parser or semantic judge.

    Remove regex syntax/classes/escapes before extracting literal words (>=2
    chars). Escaped/unicode-encoded literals can be missed; report no soundness
    claim for arbitrary regex. Single Chinese characters are too broad to flag.
    """
    pattern = re.sub(r"\[\^?(?:\\.|[^\]\\])*\]", " ", pattern)
    pattern = re.sub(r"\(\?P<[^>]+>|\(\?P=[^)]+\)", " ", pattern)
    pattern = re.sub(r"\(\?[aiLmsux-]+[:)]", " ", pattern)
    pattern = re.sub(r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|.)", " ", pattern)
    pattern = re.sub(r"\{\d+(?:,\d*)?\}", " ", pattern)
    return set(re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9_]{2,}", pattern))


def _check_reply_echo(sample: dict, report: Report, sid: str) -> None:
    """F-9 W212: warn when a hard reply-present anchor echoes reference wording.

    No rule is changed, and no warning constitutes evidence of Agent misconduct.
    Applies to historical suites too, strictly as a warning (no paper mutation).
    """
    refs = (sample.get("expect") or {}).get("reference_answer") or []
    if not isinstance(refs, list):
        return  # schema reports malformed reference_answer
    refs = [r for r in refs if isinstance(r, str)]
    for ai, anchor in enumerate(sample.get("anchors") or []):
        if not isinstance(anchor, dict) or not (
                anchor.get("type") == "reply_text" and anchor.get("severity") == "hard"
                and anchor.get("expect") == "present"):
            continue
        pattern = anchor.get("regex")
        if not isinstance(pattern, str):
            continue
        hits = []
        for token in sorted(_reply_literal_tokens(pattern)):
            if re.search(r"[\u3400-\u9fff]", token):
                overlaps = any(token in r for r in refs)
            else:
                overlaps = any(re.search(r"(?<![A-Za-z0-9_])" + re.escape(token)
                                         + r"(?![A-Za-z0-9_])", r) for r in refs)
            if overlaps:
                hits.append(token)
        if hits:
            report.add("W212", "warning",
                       f"hard reply_text present 与 reference_answer 共享字面词元 {hits}："
                       "正确改写可能因未复述参考词面而被误判（定罪词回声风险）。"
                       "请审查是否改为产物层 hard、降 soft 或登记 limitation；这不是错误行为证据。",
                       sid, f"anchors[{ai}].regex")


def _check_pii(sample: dict, report: Report, sid: str) -> None:
    declared = [p.get("id") for p in sample.get("pii") or [] if isinstance(p, dict)]
    declared_upper = {d.upper() for d in declared if isinstance(d, str)}

    # E109 pii id 唯一
    dup = {x for x in declared if declared.count(x) > 1}
    if dup:
        report.add("E109", "error", f"pii id 重复：{sorted(dup)}（哪个 value 生效由实现决定，必须唯一）", sid, "pii")

    used: set[str] = set()          # 以【规范写法】出现过的 id
    for _loc, text in _iter_texts(sample):
        for ph in PLACEHOLDER_RE.findall(text):
            used.add(ph.strip("<>"))
        # 宽松探测：形似占位符但不是规范写法（小写 / 缺右括号 / 中间有空格 / 全角）——
        # 引擎只会精确替换规范写法，这些会原样发给 Agent，边界识别维度当场失效。
        for ph in LOOSE_PLACEHOLDER_RE.findall(text):
            if PLACEHOLDER_RE.fullmatch(ph):
                continue
            report.add("E109", "error",
                       f"形似占位符但不是规范写法：{ph!r}；规范写法是 <PII_XXX>（全大写、半角、闭合的尖括号），否则引擎不会物化它",
                       sid, "pii")
    # 锚点里的占位符也算「使用」
    for a in sample.get("anchors") or []:
        if not isinstance(a, dict):
            continue
        for f in REGEX_FIELDS:
            v = a.get(f)
            if isinstance(v, str):
                for ph in PLACEHOLDER_RE.findall(v):
                    used.add(ph.strip("<>"))

    undeclared = used - set(declared)
    if undeclared:
        report.add("E109", "error",
                   f"占位符未在 pii 中声明：{sorted('<' + u + '>' for u in undeclared)}；未声明的占位符会被原样发给 Agent（边界识别维度当场失效）",
                   sid, "pii")

    unused = set(declared) - used
    if unused:
        report.add("W201", "warning", f"pii 声明了但未使用：{sorted('<' + u + '>' for u in unused)}", sid, "pii")

    # E117 占位符空间纪律：断言 / 期望里不得出现 pii 明文值。
    # 这些字符串会在【已占位符化】的证据上求值，写明文必然恒不匹配（静默假阳）。
    check_targets: list[tuple[str, str]] = []
    for ai, a in enumerate(sample.get("anchors") or []):
        if isinstance(a, dict):
            for f in REGEX_FIELDS:
                if isinstance(a.get(f), str):
                    check_targets.append((f"anchors[{ai}].{f}", a[f]))
    exp = sample.get("expect") or {}
    for i, t in enumerate(exp.get("trap") or []):
        if isinstance(t, str):
            check_targets.append((f"expect.trap[{i}]", t))
    for i, t in enumerate(exp.get("reference_answer") or []):
        if isinstance(t, str):
            check_targets.append((f"expect.reference_answer[{i}]", t))
    for p in sample.get("pii") or []:
        if not isinstance(p, dict):
            continue
        val = p.get("value")
        if not isinstance(val, str) or len(val) < 4:
            continue
        for loc, text in check_targets:
            if val in text:
                report.add("E117", "error",
                           f"{loc} 里出现了 pii 明文值（应写占位符 <{p.get('id')}>）——证据落盘时明文已换成占位符，写明文会让这条断言恒不匹配",
                           sid, loc)


def _check_paths(sample: dict, report: Report, sid: str) -> None:
    import unicodedata

    def bad(p: Any) -> str | None:
        if not isinstance(p, str) or not p:
            return "路径为空"
        if p.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", p):
            return "禁止绝对路径 / 家目录展开符 ~"
        if "\\" in p:
            return "禁止反斜杠（用 /）"
        if any(seg in ("..", ".") for seg in p.split("/")):
            return "禁止 .. 与 . 路径段"
        if "\x00" in p or p != p.strip():
            return "含 NUL 或首尾空白"
        if unicodedata.normalize("NFKC", p) != p:
            return "含全角 / 兼容等价字符（NFKC 归一化后会变成另一条路径）"
        return None

    for fi, fx in enumerate(sample.get("fixture") or []):
        if not isinstance(fx, dict):
            continue
        why = bad(fx.get("path"))
        if why:
            report.add("E111", "error", f"fixture[{fi}].path 不安全：{fx.get('path')!r} —— {why}", sid, f"fixture[{fi}].path")
    for ai, a in enumerate(sample.get("anchors") or []):
        if not isinstance(a, dict):
            continue
        # 用 "path" in a 而不是 a.get("path")：后者会让 path: "" 整段被跳过
        if "path" in a:
            why = bad(a.get("path"))
            if why:
                report.add("E111", "error", f"anchors[{ai}].path 不安全：{a.get('path')!r} —— {why}", sid, f"anchors[{ai}].path")


def _check_expect(sample: dict, report: Report, sid: str, index: dict[str, dict]) -> None:
    exp = sample.get("expect") or {}

    # E112 边界维度必须有 boundary_kind
    if sample.get("dimension") == "boundary_recognition" and not exp.get("boundary_kind"):
        report.add("E112", "error",
                   "dimension=boundary_recognition 必须填 expect.boundary_kind（withhold/release）——五分类里没有『正确地没记』，缺它会让该维度两种相反行为混装进同一分母",
                   sid, "expect.boundary_kind")

    # E113/E114 轮次指针可解析
    def check_pointers(key: str, code: str) -> None:
        for ptr in exp.get(key) or []:
            if not isinstance(ptr, str) or not TURN_RE.match(ptr):
                report.add(code, "error", f"expect.{key} 指针格式非法：{ptr!r}（应为 s3.t1）", sid, f"expect.{key}")
                continue
            m = TURN_RE.match(ptr)
            sess_id, turn_no = f"s{m.group(1)}", int(m.group(2))
            if sess_id not in index or turn_no not in index[sess_id]:
                report.add(code, "error", f"expect.{key} 指向不存在的轮次：{ptr}", sid, f"expect.{key}")

    check_pointers("gold_evidence", "E113")
    check_pointers("supersedes", "E114")

    # W205 verdict 缺失（schema 已查 required，兜底）
    if exp.get("verdict") not in VERDICTS:
        report.add("E001", "error", f"expect.verdict 非法：{exp.get('verdict')!r}", sid, "expect.verdict")


def _check_vacuity(sample: dict, report: Report, sid: str) -> None:
    """E119 / W205 / W206 —— 三条专门防“断言看似存在、实际不千活”的检查。

    这三条都是评审实测出来的真实缺陷类型，加进工具以防复发。
    """
    anchors = [a for a in (sample.get("anchors") or []) if isinstance(a, dict)]
    fixtures = {fx.get("path"): (fx.get("content") or "") for fx in (sample.get("fixture") or []) if isinstance(fx, dict)}

    # 判定每条 hard+present 锚点是否“空转可满足”（Agent 什么都不做也能过）
    def vacuous(a: dict) -> bool:
        if a.get("type") != "file_content":
            return False  # 回复/工具调用/记忆类断言必须由 Agent 动作才能满足
        if a.get("must_differ_from_fixture"):
            return False  # 要求有改动，fixture 原值无法满足
        path, rx = a.get("path"), a.get("content_regex")
        if not isinstance(rx, str):
            return path in fixtures  # 只断言“存在”，而 fixture 里本来就有
        try:
            if path in fixtures and re.search(rx, fixtures[path]):
                return True
            # 跨文件复制也算：只要【任何】fixture 文件的内容能命中，
            # Agent 把它复制成 path 就能过关（评审提出的“复制另一份 fixture 即满足”）。
            # 仅当 path 不在 fixture 中时才检查——否则同文件已由上一行覆盖。
            if path not in fixtures:
                return any(isinstance(c, str) and re.search(rx, c) for c in fixtures.values())
            return False
        except re.error:
            return False

    hard_pos = [a for a in anchors if a.get("severity") == "hard" and a.get("expect") == "present"]
    if hard_pos and all(vacuous(a) for a in hard_pos):
        report.add("E119", "error",
                   "样本没有任何有效正向门槛：全部 hard+present 锚点都能被 fixture 原值满足，"
                   "Agent 空转（或崩溃）也能通吃所有负证据拿满分。请补一条依赖 Agent 动作的 hard+present 断言"
                   "（reply_text / tool_call / memory_entry）",
                   sid, "anchors")
    else:
        for ai, a in enumerate(anchors):
            if a.get("severity") == "hard" and a.get("expect") == "present" and vacuous(a):
                report.add("W207", "warning",
                           f"anchors[{ai}] 单独看无区分力：它的内容在 fixture 上已经成立，Agent 不改这个文件也能通过"
                           f"（样本整体仍有别的正向门槛，故不阻断；若这是唯一的产物断言，请加 must_differ_from_fixture 或改断言语义）",
                           sid, f"anchors[{ai}]")

    # W205 多违规时的 verdict 优先级未声明：不同 hard 锚点的 on_violation 不一致
    hard_violations = [a.get("on_violation") for a in anchors if a.get("severity") == "hard" and a.get("on_violation")]
    distinct = [v for v in dict.fromkeys(hard_violations) if hard_violations.count(v) >= 1]
    if len(set(hard_violations)) > 1:
        report.add("W205", "warning",
                   f"本样本的 hard 锚点声明了 {len(set(hard_violations))} 种不同的 on_violation {sorted(set(hard_violations))}；"
                   f"同时违反多条时的定类优先级由 CONTRACT §5 规定（首条命中的 hard 锚点优先），请确认这个顺序符合预期",
                   sid, "anchors")

    # W206 expect.trap 声明的诱错值没有任何锚点覆盖
    trap = (sample.get("expect") or {}).get("trap") or []
    if trap:
        from trap_expand import covered_by_anchor
    for i, t in enumerate(trap):
        if not isinstance(t, str) or not t:
            continue
        if covered_by_anchor(t, anchors):
            continue
        report.add("W206", "warning",
                   f"expect.trap[{i}] = {t!r} 没有被任何锚点覆盖：可用 `build_paper --expand-traps` "
                   f"按 CONTRACT §5 自动展开为 memory_entry+absent soft 锚点；未展开时这条 trap 只是注释",
                   sid, f"expect.trap[{i}]")
    # W208 事件类锚点依赖事件流采集，而“采集不到”与“真的没发生”不可区分
    for ai, a in enumerate(anchors):
        if a.get("type") in ("memory_read", "memory_write") and a.get("evaluate_at") == "any_time":
            report.add("W208", "warning",
                       f"anchors[{ai}] 是事件流断言（type={a.get('type')} + any_time）：若适配器未采集该类事件，"
                       f"该锚点会**恒真**（absent）或**恒假**（present），静默把环境缺失记成 Agent 行为。"
                       f"引擎必须能声明该类事件的覆盖率，否则本锚点应整体 skip（见 CONTRACT §15 #7）",
                       sid, f"anchors[{ai}]")


def _is_legacy(sample: dict, file_suite: str = "") -> bool:
    """显式 meta.suite_version 优先，缺省用文件级兼底（v0.1 存在 5 条漏写 meta 的样本，
    预注册锁定不可改）。仅 v0.1 为对照组降级；其余按现行规则。"""
    sv = str(((sample.get("meta") or {}).get("suite_version")) or file_suite or "")
    return sv == "v0.1"


def _check_verdict_contract(sample: dict, report: Report, sid: str, file_suite: str = "") -> None:
    """E120 / W209 / E121 —— 把 C-2 实验实证的 D 类缺陷固化成闸（见 notes/c2-*）。

    E120 声明可达性：expect.confusions 里每个失败形态必须有 hard 锚点的 on_violation 承接
         （否则规则路永远产不出该类——R9 穷举实证：S5 全档挤向 confused 的根因）。
         判官路（judge_only/hybrid soft）不能当可达性的兑现方：那是未上线的依赖。
    E121 未定义组合：评分器会直接 env_error 的组合（absent+max_matches / file_content+turn
         / must_differ+非present），在样本层前挡，不烧完 run 才发现。
    W209 无效组合：评分器会静默忽略的组合（提醒作者它不干活，不算错）。
    """
    legacy = _is_legacy(sample, file_suite)
    anchors = [a for a in sample.get("anchors", []) if isinstance(a, dict)]
    exp = sample.get("expect", {}) or {}
    policy = sample.get("score_policy", "hybrid")

    # E120
    declared = {c for c in (exp.get("confusions") or [])}
    if not declared and policy != "judge_only" and not (sample.get("meta") or {}).get("limitation"):
        # W211：不声明失败形态 = E120 无从把关（五类之一的正当空声明只有 judge_only/limitation 可豁免）
        report.add("W211", "warning",
                   "expect.confusions 未声明：该样本的失败形态对规则路不可见，E120 无法工作。"
                   "至少声明一个可判形态并配 hard 承接锚点", sid, "expect.confusions")
    if declared and policy != "judge_only":
        # 收紧（2026-09-22 评审）：事件类锚点自身不可判（memory_read/tool_call 无采集恒 skip；
        # memory_write+absent+any_time 恒 skip），不得充当可达性承接——防“死锚点骗过 E120”
        def violable(a):
            if a.get("type") in ("memory_read", "tool_call"):
                return False
            if a.get("type") == "memory_write" and a.get("expect") == "absent" \
                    and a.get("evaluate_at") == "any_time":
                return False
            return True
        reachable = {a.get("on_violation") for a in anchors
                     if a.get("severity") == "hard" and a.get("on_violation") and violable(a)}
        if exp.get("verdict"):
            reachable.add(exp["verdict"])
        missing = declared - reachable
        if missing:
            report.add("E120", "warning" if legacy else "error",
                       f"expect.confusions 中 {sorted(missing)} 在规则路不可达：没有任何 hard 锚点的 "
                       f"on_violation 是它们。失败形态只能靠未上线的判官碰——补 hard 锚点或从 "
                       f"confusions 移除（v0.1 对照组降级为 warning）", sid, "expect.confusions")

    # E121 / W209
    for idx, a in enumerate(anchors):
        atype, expect = a.get("type"), a.get("expect")
        loc = f"anchors[{idx}]"
        if a.get("max_matches") is not None:
            if expect == "absent":
                report.add("E121", "warning" if legacy else "error",
                           f"{a.get('id')}: absent 配 max_matches 无定义（评分器判 env_error，不猜）", sid, loc)
            elif not (atype == "memory_entry" and expect == "present"):
                report.add("W209", "warning",
                           f"{a.get('id')}: max_matches 仅在 memory_entry+present 生效，当前组合被评分器忽略", sid, loc)
        if a.get("must_differ_from_fixture") or a.get("must_equal_fixture"):
            flag = "must_differ_from_fixture" if a.get("must_differ_from_fixture") else "must_equal_fixture"
            if atype != "file_content":
                report.add("W209", "warning", f"{a.get('id')}: {flag} 对 {atype} 无效（仅 file_content）", sid, loc)
            elif a.get("must_differ_from_fixture") and expect != "present":
                report.add("E121", "warning" if legacy else "error",
                           f"{a.get('id')}: must_differ 仅定义于 expect=present（其余组合评分器 env_error）", sid, loc)
        if a.get("turn") and atype == "file_content":
            report.add("E121", "warning" if legacy else "error",
                       f"{a.get('id')}: file_content+turn 当前不可判——file_write 行无轮次归属（ISS-09），"
                       f"评分器会 env_error；A 落地归属前改用 reply/memory 锚点或等 ISS 收口", sid, loc)
        if a.get("evaluate_at") == "snapshot_before" and atype in ("reply_text", "file_content"):
            report.add("W209", "warning",
                       f"{a.get('id')}: {atype}+snapshot_before 无定义（对话未发生/无前置清单），评分器 skip 或 env", sid, loc)


def _check_pii_collisions(samples: list[dict], report: Report) -> None:
    """W210 跨样本 PII 冲突：一个样本声明的敏感值，若以明文出现在另一样本任何字段
    （C-2 实证 D1：13800000001 同时是 update_multi 的声明值与 similar_contacts 的话术明文，
    导致 provenance 永远无法全局 grep 收紧）。声明值自身样本不计；值长 <6 不查（误报多）。"""
    declared: dict[str, list[str]] = {}
    for s in samples:
        for e in s.get("pii", []) or []:
            v = str(e.get("value", ""))
            if len(v) >= 6:
                declared.setdefault(v, []).append(str(s.get("id", "-")))
    if not declared:
        return
    import json as _json

    def rx_literals(blob_txt: str) -> set[str]:
        """把锚点正则里的字面片段拆出来（(?…) 剔除、[x]→x、去转义/量词），
        堵「声明值以 1381234[5]789 形态藏进他样本正则」的 W210 盲区（momus 向量④）。"""
        out = set()
        for m in re.finditer(r'"(?:value|content|arg)?_?regex"\s*:\s*"([^"]+)"', blob_txt):
            s = re.sub(r"\(\?[^)]*\)", "", m.group(1))
            s = re.sub(r"\[([^\]]*)\]", r"\1", s)
            for piece in re.split(r"[\\.*+?{}()|^$]", s):
                if len(piece) >= 6:
                    out.add(piece)
        return out
    dumps = {str(s.get("id", "-")): _json.dumps(s, ensure_ascii=False) for s in samples}
    for val, owners in declared.items():
        for oid in owners:
            for sid, blob in dumps.items():
                if sid in owners:
                    continue
                if val not in blob and not any(val in lit or lit in val for lit in rx_literals(blob)):
                    continue
                msg = (f"声明值（{owners[0]} 的 pii）以明文出现在样本 {sid} 中——provenance 只能按归属审，"
                       f"无法全局收紧；换声明值或改该样本话术（C-2 D1 同类）：{val[:4]}…")
                report.add("W210", "warning", msg, sid, "pii")
                report.add("W210", "warning", msg, owners[0], "pii")


def loader_validate(sample: dict, report: Report, file_suite: str = "") -> None:
    sid = sample.get("id", "-")
    index = _check_script(sample, report, sid)
    _check_anchors(sample, report, sid, index)
    _check_reply_echo(sample, report, sid)
    _check_vacuity(sample, report, sid)
    _check_verdict_contract(sample, report, sid, file_suite)
    _check_pii(sample, report, sid)
    _check_paths(sample, report, sid)
    _check_expect(sample, report, sid, index)


# ── 编排 ────────────────────────────────────────────────────────────────────

def validate_samples(samples: list[dict], schema: dict | None = None, file_suite: str = "") -> Report:
    report = Report()
    if schema is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    # E115 样本 id 跨样本唯一
    ids = [s.get("id") for s in samples if isinstance(s, dict)]
    for dup in {i for i in ids if ids.count(i) > 1}:
        report.add("E115", "error", f"样本 id 重复：{dup}（run 目录与 verdicts 会互相覆盖）", str(dup), "id")

    for s in samples:
        if not isinstance(s, dict):
            report.add("E001", "error", f"样本必须是对象，实际是 {type(s).__name__}", "-")
            continue
        schema_validate(s, report, schema)
        loader_validate(s, report, file_suite)
    _check_pii_collisions([s for s in samples if isinstance(s, dict)], report)
    return report


def validate_path(path: str) -> Report:
    file_sv, samples = load_any(path)
    return validate_samples(samples, file_suite=str(file_sv or ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="okb 样本校验器（schema + loader 两层）")
    ap.add_argument("paths", nargs="+", help=".yaml / .yml / .jsonl 文件")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（供 CI 消费）")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    ap.add_argument("--strict", action="store_true", help="把 warning 也当失败")
    args = ap.parse_args(argv)

    all_reports: list[tuple[str, Report]] = []
    for p in args.paths:
        try:
            rep = validate_path(p)
        except Exception as exc:  # 读取/解析失败
            print(f"FATAL   {p}: {exc}", file=sys.stderr)
            return 2
        all_reports.append((p, rep))

    if args.json:
        print(json.dumps(
            [{"source": src, "issues": [asdict(i) for i in rep.issues]} for src, rep in all_reports],
            ensure_ascii=False, indent=2))
    else:
        for src, rep in all_reports:
            if not args.quiet:
                for i in rep.issues:
                    print(i.render(src))
        total_err = sum(len(r.errors) for _, r in all_reports)
        total_warn = sum(len(r.warnings) for _, r in all_reports)
        print(f"\n{'─' * 60}\n检查 {len(all_reports)} 个文件：{total_err} error / {total_warn} warning")

    total_err = sum(len(r.errors) for _, r in all_reports)
    total_warn = sum(len(r.warnings) for _, r in all_reports)
    if total_err:
        return 1
    if args.strict and total_warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
