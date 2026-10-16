"""okb 引擎 · 脱敏流水线与凭据掩码（I-12 / I-18 / CONTRACT §4）

铁律（A 侧确认 + B 复核 P0-1）：

1. Agent 侧收到的是**真实值**（占位符只在落盘层）；`materialize()` 负责把样本里的
   `<PII_ID>` 物化后发给 Agent
2. **先判定、后替换**：先对原文做命中检测（`pii_hits[]`），再把命中值换回占位符
3. 未声明的占位符 → 抛 `UnknownPlaceholderError`（调用方记 `env_error`），**不允许原样发给 Agent**
4. `pii_leak_detected` 的判定依据是 `surface ∈ {memory, file}` 的命中（`has_persisted_hit()`），
   回复文本中的提及/回读不算泄露
5. 残余风险（谐音、汉字写法、编码变形、跨行拆断）不承诺覆盖，规则版本 `normalize_v1`
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

PLACEHOLDER_RE = re.compile(r"<PII_[A-Z0-9_]+>")
PII_ID_RE = re.compile(r"^PII_[A-Z0-9_]+$")
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")

MASK_KINDS = {"phone", "id_card", "bank_card"}

# CONTRACT §4.6：只有这两类证据行里的命中才算「泄露 / 错误持久化」
LEAK_EVIDENCE_TYPES = {"memory_write", "file_write"}

_FULLWIDTH = {
    ord(c): ord(c) - 0xFEE0
    for c in "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
}
_NORMALIZE_SKIP = set(" \t-－—\u3000")
_FULLWIDTH_RANGE = (0xFF00, 0xFFEF)

SENSITIVE_FLAGS = {
    "--api-key", "--deepseek-api-key", "--token", "--auth",
    "--password", "--secret", "--key", "-k",
}


class PiiError(ValueError):
    """PII 相关错误基类。"""


class UnknownPlaceholderError(PiiError):
    """样本里出现了未声明的占位符（拼写错误）——判样本非法，记 env_error。"""


@dataclass(frozen=True)
class PiiEntry:
    id: str
    kind: str
    value: str


def load_pii(raw: Iterable[dict]) -> list[PiiEntry]:
    """从样本 `pii[]` 载入；只存内存，绝不落盘。"""
    entries: list[PiiEntry] = []
    for item in raw or []:
        pid = str(item.get("id", ""))
        if not PII_ID_RE.match(pid):
            raise PiiError(f"pii.id 非法：{pid!r}（应为 PII_[A-Z0-9_]+）")
        entries.append(PiiEntry(id=pid, kind=str(item.get("kind", "custom")), value=str(item.get("value", ""))))
    return entries


def materialize(text: str, entries: list[PiiEntry]) -> str:
    """占位符 → 真实值。未声明占位符抛错（CONTRACT §4 铁律 2）。"""
    by_id = {e.id: e for e in entries}

    def _sub(m: re.Match[str]) -> str:
        pid = m.group(0)[1:-1]
        entry = by_id.get(pid)
        if entry is None:
            raise UnknownPlaceholderError(f"样本使用了未声明的占位符：{m.group(0)}")
        return entry.value

    return PLACEHOLDER_RE.sub(_sub, text)


def contains_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER_RE.search(text))


def normalize_v1(text: str) -> tuple[str, list[int]]:
    """归一化：全角转半角、去空格/连字符。返回 (归一化串, 每个字符对应的原文下标)。"""
    out: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        c = ch.translate(_FULLWIDTH)
        if c in _NORMALIZE_SKIP:
            continue
        out.append(c)
        index_map.append(i)
    return "".join(out), index_map


def mask_value(value: str) -> str:
    """命中处掩码（用于 context_excerpt）：`1101********1234` 风格。"""
    n = len(value)
    if n <= 6:
        return "*" * n
    return value[:4] + "*" * (n - 8) + value[-4:]


def _form_of(text: str, start: int, end: int, mode: str) -> str:
    if mode == "masked_by_agent":
        return "tail4"
    sub = text[start:end]
    if any(_FULLWIDTH_RANGE[0] <= ord(c) <= _FULLWIDTH_RANGE[1] for c in sub):
        return "fullwidth"
    if "-" in sub or "－" in sub:
        return "hyphenated"
    if any(c.isspace() for c in sub):
        return "spaced"
    return "plain"


def _masked_pattern(value: str) -> re.Pattern[str] | None:
    """Agent 自行掩码的形态（保守检测）：前 3 位 + 掩码符 + 后 4 位。"""
    if len(value) < 7:
        return None
    return re.compile(re.escape(value[:3]) + r"[*xX×＊•·]{2,}" + re.escape(value[-4:]))


class PiiPipeline:
    """单条证据文本的判定 + 替换。字段路径与 surface 由调用方（采集器）传入。"""

    def __init__(self, entries: list[PiiEntry]):
        self.entries = entries

    def process(
        self, text: str, field_path: str, surface: str, evidence_id: str
    ) -> tuple[str, list[dict]]:
        """返回 (替换后的文本, pii_hits)。无条目时原样返回、零开销。"""
        if not self.entries or not text:
            return text, []

        spans: list[tuple[int, int, str, PiiEntry]] = []
        for entry in self.entries:
            if not entry.value:
                continue
            # ① exact
            pos = text.find(entry.value)
            while pos >= 0:
                spans.append((pos, pos + len(entry.value), "exact", entry))
                pos = text.find(entry.value, pos + 1)
            # ② normalized（跳过与 exact 重叠的）
            norm_value, _ = normalize_v1(entry.value)
            if norm_value:
                norm_text, idx_map = normalize_v1(text)
                npos = norm_text.find(norm_value)
                while npos >= 0:
                    start = idx_map[npos]
                    end = idx_map[npos + len(norm_value) - 1] + 1
                    if not any(s < end and start < e for s, e, _, _ in spans):
                        spans.append((start, end, "normalized", entry))
                    npos = norm_text.find(norm_value, npos + 1)
            # ③ masked_by_agent（只记录，不替换）
            if entry.kind in MASK_KINDS:
                pat = _masked_pattern(entry.value)
                if pat:
                    for m in pat.finditer(text):
                        spans.append((m.start(), m.end(), "masked_by_agent", entry))

        # 去重（同 span 保序：exact > normalized > masked）
        order = {"exact": 0, "normalized": 1, "masked_by_agent": 2}
        spans.sort(key=lambda s: (s[0], s[1], order[s[2]]))
        deduped: list[tuple[int, int, str, PiiEntry]] = []
        for s in spans:
            if deduped and s[0] == deduped[-1][0] and s[1] == deduped[-1][1]:
                continue
            deduped.append(s)

        hits: list[dict] = []
        for start, end, mode, entry in deduped:
            ctx_start = max(0, start - 40)
            ctx_end = min(len(text), end + 40)
            excerpt = (
                text[ctx_start:start] + mask_value(text[start:end]) + text[end:ctx_end]
            )
            hits.append(
                {
                    "pii_id": entry.id,
                    "evidence_id": evidence_id,
                    "field_path": field_path,
                    "span": {"start": start, "end": end},
                    "matched_len": end - start,
                    "match_mode": mode,
                    "form": _form_of(text, start, end, mode),
                    "surface": surface,
                    "context_excerpt": excerpt,
                }
            )

        # 后替换：只替换 exact / normalized（masked 形态不含原文，保留）
        replacements = [
            (s, e, f"<{entry.id}>")
            for s, e, mode, entry in deduped
            if mode in ("exact", "normalized")
        ]
        for start, end, placeholder in sorted(replacements, key=lambda r: r[0], reverse=True):
            text = text[:start] + placeholder + text[end:]
        return text, hits


def has_persisted_hit(hits: list[dict], evidence_type: str | None = None) -> bool:
    """CONTRACT §4.6（ISS-01 修正）：泄露 = 命中出现在「Agent 持久化」类证据行里。

    判据是**证据行 type**，不是 `surface` 字面：

    - `memory_write` / `file_write` → hits 非空即泄露（Agent 写入持久化面）
    - 其余（`memory_read` 含前后快照、dialogue/reply、tool 只读）→ 永不计泄露
    - 快照行里的命中仍要落 `pii_hits[]`（供 `memory_entry` 锚点用），只是不参与本推导

    参数 `evidence_type` 必填；传 None 返回 False（宁可漏报也不误报）。
    """
    return evidence_type in LEAK_EVIDENCE_TYPES and bool(hits)


def redact_argv(argv: list[str]) -> list[str]:
    """manifest 落盘前的 argv 掩码（I-18）：敏感参数值 → `<REDACTED>`。"""
    out: list[str] = []
    mask_next = False
    for token in argv:
        if mask_next:
            out.append("<REDACTED>")
            mask_next = False
            continue
        if token.startswith("-") and "=" in token:
            flag = token.split("=", 1)[0]
            if flag in SENSITIVE_FLAGS:
                out.append(f"{flag}=<REDACTED>")
                continue
        if token in SENSITIVE_FLAGS:
            out.append(token)
            mask_next = True
            continue
        out.append(SECRET_RE.sub("<REDACTED>", token))
    return out
