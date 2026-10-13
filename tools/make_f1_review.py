#!/usr/bin/env python3
"""F1 人工审题包生成器（2026-10-09，A 侧）。

把交付卷（samples/paper-r1.yaml，人读源）渲染成**离线可用的审题单**：
- review-sheet.html：一题一卡（题面/锚点/答案位/机器提示 + 勾选项 + 备注），
  Chrome/Edge 本地打开，零网络、零上传，可存草稿、可导出 JSON；
- review-worklist.json：机器可读题单（含 paper_sha256 绑定），供 --check 校验回传件；
- README.md：人审流程（纪律/职责/回传/边界）。

纪律：审题人只看题面/锚点/答案位本身，**不得看跑批分数/判官输出**（防事后拟合）；
作者不能自审；本包不代替 κ 标注与仪器复核。

用法：
    python tools/make_f1_review.py --out-dir handoff/f1-review-2026-10-09
    python tools/make_f1_review.py --out-dir … --sample-id update_delayed_001,boundary_risk_001
    python tools/make_f1_review.py --check f1-labels-张三.json \
        --worklist handoff/f1-review-2026-10-09/review-worklist.json
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from loader import load_any  # noqa: E402

FLAGS = ("ok", "ambiguous", "wrong_key", "anchor_mismatch", "unjudgeable", "other")
SEVERITIES = ("must_fix", "suggest", "note")
FORMAT = "okb-f1-review-worklist-v1"
LABELS_FORMAT = "okb-f1-review-labels-v1"

SEC_KINDS = ("value_regex", "key_regex", "content_regex", "regex", "arg_regex")


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_anchorcal(path: pathlib.Path) -> dict[str, list[str]]:
    """anchorcal 凭据 → {sample_id: [机器提示...]}；文件缺失即空（提示为可选增强）。"""
    hints: dict[str, list[str]] = {}
    if not path.is_file():
        return hints
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return hints
    mapping = (
        ("path_never_produced", "路径钉死"),
        ("unverified_negative", "负判据不可验证"),
        ("evidence_absent_not_predicate", "证据面缺失（非判据问题）"),
        ("evidence_absent_probe_unverified", "探针未证实（采集问题）"),
        ("suspect_dead_predicate", "死锚嫌疑"),
    )
    for key, label in mapping:
        for item in data.get(key) or []:
            if not isinstance(item, dict) or not item.get("sample_id"):
                continue
            anchor = item.get("anchor_id") or "?"
            detail = str(item.get("detail") or "").strip()
            hints.setdefault(item["sample_id"], []).append(f"[{label}] {anchor}: {detail}")
    return hints


def load_plan(path: pathlib.Path | None) -> dict[str, list[dict]]:
    """四层计划 → {sample_id: [checks...]}；文件缺失或样例缺失即空。"""
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {row.get("sample_id"): row.get("checks") or [] for row in data.get("samples") or []}


def render_checks(checks: list[dict]) -> str:
    """计划判据区块：对 75 张卡派生题，这才是真正的评分依据（纸面锚点多为结构性）。"""
    if not checks:
        return "<p class='muted'>（计划中没有该题的检查项——需标问题）</p>"
    out = []
    for check in checks:
        head = (f"{check.get('id')} · layer={check.get('layer')} · "
                f"on_failure={check.get('on_failure')} · scope={check.get('scope')}")
        if check.get("session"):
            head += f" · {check['session']}.t{check.get('turn')}"
        req = html.escape(str(check.get("requirement") or ""))
        out.append(f"<div class='check'><div class='check-head'>{html.escape(head)}</div>"
                   f"<div class='check-req'>{req}</div></div>")
    return "".join(out)


def anchor_lines(anchor: dict) -> list[str]:
    parts = [f"type={anchor.get('type')}", f"severity={anchor.get('severity')}",
             f"expect={anchor.get('expect')}"]
    if anchor.get("on_violation"):
        parts.append(f"on_violation={anchor['on_violation']}")
    if anchor.get("evaluate_at"):
        parts.append(f"evaluate_at={anchor['evaluate_at']}")
    for field in ("key", "key_regex", "value_regex", "path", "field_path", "content_regex",
                  "turn", "regex", "action", "arg_regex", "max_matches"):
        if anchor.get(field) is not None:
            parts.append(f"{field}={anchor[field]!r}")
    return parts


def render_question(index: int, sample: dict, hints: dict[str, list[str]],
                    plan_checks: list[dict] | None = None) -> str:
    sid = html.escape(str(sample.get("id")))
    dim = html.escape(str(sample.get("dimension")))
    difficulty = html.escape(str(sample.get("difficulty")))
    policy = html.escape(str(sample.get("score_policy")))
    title = html.escape(str(sample.get("title") or ""))
    meta = sample.get("meta") or {}
    source = html.escape(str(meta.get("source") or ""))
    limitation = html.escape(str(meta.get("limitation") or ""))

    sessions = []
    for sess in sample.get("script") or []:
        rows = []
        for i, turn in enumerate(sess.get("turns") or [], start=1):
            text = html.escape(str(turn.get("text") or ""))
            rows.append(f"<li><span class='turn'>t{i}</span>{text}</li>")
        time = f" · time={html.escape(str(sess.get('time')))}" if sess.get("time") else ""
        sessions.append(
            f"<div class='session'><div class='sess-head'>session={html.escape(str(sess.get('session')))}"
            f" · phase={html.escape(str(sess.get('phase')))}{time}</div><ol>{''.join(rows)}</ol></div>")

    anchors = []
    for a in sample.get("anchors") or []:
        desc = html.escape(str(a.get("desc") or ""))
        anchors.append(
            f"<div class='anchor'><div class='anchor-head'>{html.escape(str(a.get('id')))}"
            f" <span class='muted'>{html.escape(' · '.join(anchor_lines(a)))}</span></div>"
            + (f"<div class='anchor-desc'>{desc}</div>" if desc else "") + "</div>")

    expect = sample.get("expect") or {}
    expect_rows = []
    for key in ("verdict", "confusions", "boundary_kind", "gold_evidence", "trap",
                "reference_answer"):
        if expect.get(key) is not None:
            expect_rows.append(f"<tr><th>{key}</th><td>{html.escape(json.dumps(expect[key], ensure_ascii=False))}</td></tr>")
    if expect.get("notes"):
        expect_rows.append(f"<tr><th>notes</th><td>{html.escape(str(expect['notes']))}</td></tr>")

    hint_box = ""
    if hints.get(str(sample.get("id"))):
        items = "".join(f"<li>{html.escape(h)}</li>" for h in hints[str(sample.get("id"))])
        hint_box = ("<div class='hints'><b>机器提示（anchorcal，仅供参考，不是结论）</b>"
                    f"<ul>{items}</ul></div>")

    radios = []
    labels = (("ok", "✅ 无问题"), ("ambiguous", "⚠️ 题面歧义"),
              ("wrong_key", "❌ 答案或判据有误"), ("anchor_mismatch", "🔀 锚点与意图不符"),
              ("unjudgeable", "❓ 不可判"), ("other", "✏️ 需改（其他）"))
    for value, label in labels:
        radios.append(f"<label class='opt'><input type='radio' name='flag-{sid}' value='{value}'>{label}</label>")

    return f"""
<section class="q" id="q-{sid}" data-sample="{sid}">
  <div class="q-head"><span class="q-no">#{index}</span>
    <b>{sid}</b> <span class="muted">{dim} · {difficulty} · {policy}</span>
    <span class="state" id="state-{sid}">未审</span></div>
  <div class="title">{title}</div>
  <div class="cols">
    <div class="col">
      <h4>题面 / 脚本（用户看得见的话）</h4>
      {''.join(sessions)}
      <h4>锚点（规则层；卡派生题多为结构性，只防空转）</h4>
      {''.join(anchors)}
      <h4>判据（四层计划 · 本题实际评分依据）</h4>
      {render_checks(plan_checks or [])}
    </div>
    <div class="col">
      <h4>答案位（判官与 Agent 均不可见；审题人用于核对）</h4>
      <table class="expect">{''.join(expect_rows)}</table>
      <details><summary>来源与 limitation</summary>
        <div class="muted">source: {source}</div>
        <div class="muted">{limitation}</div></details>
      {hint_box}
    </div>
  </div>
  <div class="review">
    <div class="radios">{''.join(radios)}</div>
    <div class="sev">
      严重度：
      <label><input type="radio" name="sev-{sid}" value="must_fix">必改</label>
      <label><input type="radio" name="sev-{sid}" value="suggest" checked>建议</label>
      <label><input type="radio" name="sev-{sid}" value="note">仅记录</label>
    </div>
    <textarea id="note-{sid}" rows="2" placeholder="理由/建议改法（必改必须写：为什么 + 怎么改）"></textarea>
  </div>
</section>"""


CSS = """
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;background:#f5f6f8;color:#1c1e21}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #d8dade;padding:10px 16px;z-index:9}
h1{font-size:18px;margin:4px 0}
.muted{color:#6b7280;font-size:12px}
.bar{margin-top:6px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:13px}
button{padding:6px 12px;border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;cursor:pointer}
button.ghost{background:#fff;color:#2563eb}
button[disabled]{opacity:.5;cursor:default}
main{max-width:1180px;margin:0 auto;padding:14px}
.q{background:#fff;border:1px solid #e2e4e8;border-radius:10px;margin:14px 0;padding:12px 16px}
.q-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.q-no{color:#9ca3af}
.state{margin-left:auto;font-size:12px;color:#6b7280}
.state.done{color:#059669}
.state.flag{color:#dc2626}
.title{font-weight:600;margin:6px 0}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
h4{margin:10px 0 6px;font-size:13px;color:#374151}
.session{margin:6px 0;border-left:3px solid #e5e7eb;padding-left:8px}
.sess-head{font-size:12px;color:#6b7280}
.turn{display:inline-block;min-width:26px;color:#9ca3af;font-size:12px}
ol{margin:4px 0 4px 18px;padding:0}
.anchor{border:1px dashed #e5e7eb;border-radius:6px;padding:4px 8px;margin:4px 0;font-size:13px}
.anchor-desc{font-size:12px;color:#4b5563;margin-top:2px}
.check{border:1px solid #dbeafe;background:#f8fbff;border-radius:6px;padding:4px 8px;margin:4px 0;font-size:13px}
.check-head{font-size:12px;color:#2563eb}
.check-req{font-size:12px;color:#374151;margin-top:2px}
.expect{width:100%;border-collapse:collapse;font-size:12px}
.expect th{text-align:left;width:110px;color:#6b7280;vertical-align:top;padding:2px 6px 2px 0}
.expect td{padding:2px 0;word-break:break-word}
.hints{background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:6px 10px;font-size:12px;margin-top:8px}
.hints ul{margin:4px 0 0 16px}
.review{margin-top:10px;border-top:1px solid #f0f1f3;padding-top:8px}
.opt{margin-right:14px;font-size:13px}
.sev{margin:6px 0;font-size:13px}
.sev label{margin-right:10px}
textarea{width:100%;box-sizing:border-box;border:1px solid #d1d5db;border-radius:6px;padding:6px;font-size:13px}
footer{max-width:1180px;margin:0 auto;padding:10px 14px 40px;color:#6b7280;font-size:12px}
"""

JS = r"""
const WORKLIST = __WORKLIST__;
const paper = WORKLIST.paper_sha256;
const draftKey = 'okb-f1-draft-' + paper;
function collect() {
  const reviewer = document.getElementById('reviewer').value.trim();
  const date = document.getElementById('review-date').value;
  const items = WORKLIST.questions.map(q => {
    const flag = document.querySelector(`input[name="flag-${q.sample_id}"]:checked`);
    const sev = document.querySelector(`input[name="sev-${q.sample_id}"]:checked`);
    const note = document.getElementById('note-' + q.sample_id).value.trim();
    return {sample_id: q.sample_id, flag: flag ? flag.value : null,
            severity: sev ? sev.value : null, note: note};
  });
  return {format: WORKLIST.labels_format, paper_sha256: paper, reviewer: reviewer,
          date: date, items: items};
}
function refreshStates() {
  let done = 0, flagged = 0;
  for (const q of WORKLIST.questions) {
    const flag = document.querySelector(`input[name="flag-${q.sample_id}"]:checked`);
    const el = document.getElementById('state-' + q.sample_id);
    if (!flag) { el.textContent = '未审'; el.className = 'state'; continue; }
    done++;
    if (flag.value === 'ok') { el.textContent = '✅ 无问题'; el.className = 'state done'; }
    else { flagged++; el.textContent = '⚠ 已标问题'; el.className = 'state flag'; }
  }
  document.getElementById('progress').textContent = `已审 ${done} / ${WORKLIST.questions.length}（标问题 ${flagged}）`;
}
function saveDraft() { localStorage.setItem(draftKey, JSON.stringify(collect())); }
function loadDraft() {
  try {
    const d = JSON.parse(localStorage.getItem(draftKey) || 'null');
    if (!d) return;
    document.getElementById('reviewer').value = d.reviewer || '';
    document.getElementById('review-date').value = d.date || '';
    for (const it of d.items || []) {
      if (it.flag) { const el = document.querySelector(`input[name="flag-${it.sample_id}"][value="${it.flag}"]`); if (el) el.checked = true; }
      if (it.severity) { const el = document.querySelector(`input[name="sev-${it.sample_id}"][value="${it.severity}"]`); if (el) el.checked = true; }
      if (it.note) { const el = document.getElementById('note-' + it.sample_id); if (el) el.value = it.note; }
    }
  } catch (e) { console.warn(e); }
}
function exportLabels() {
  const data = collect();
  const missing = data.items.filter(i => !i.flag).length;
  if (!data.reviewer) { alert('请先填写审题人姓名'); return; }
  if (missing > 0 && !confirm(`还有 ${missing} 题未勾选，仍要导出吗？（未勾选将记为 null）`)) return;
  const blob = new Blob([JSON.stringify(data, null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'f1-labels-' + (data.reviewer || 'anon') + '.json';
  a.click();
  URL.revokeObjectURL(a.href);
}
function jumpUnreviewed() {
  for (const q of WORKLIST.questions) {
    if (!document.querySelector(`input[name="flag-${q.sample_id}"]:checked`)) {
      document.getElementById('q-' + q.sample_id).scrollIntoView({behavior: 'smooth', block: 'start'});
      return;
    }
  }
  alert('全部已审。');
}
document.addEventListener('input', () => { refreshStates(); saveDraft(); });
document.addEventListener('change', () => { refreshStates(); saveDraft(); });
window.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('review-date').value) {
    document.getElementById('review-date').value = new Date().toISOString().slice(0, 10);
  }
  loadDraft(); refreshStates();
});
"""


def build_html(worklist: dict, questions_html: str) -> str:
    embedded = json.dumps(worklist, ensure_ascii=False).replace("</", "<\\/")
    js = JS.replace("__WORKLIST__", embedded)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>okb · F1 人工审题单</title><style>{CSS}</style></head>
<body>
<header>
  <h1>okb · F1 人工审题单</h1>
  <div class="muted">卷：{html.escape(worklist['paper'])} · sha256 <code>{worklist['paper_sha256'][:16]}…</code>
    · 共 {len(worklist['questions'])} 题 · 生成于 {html.escape(worklist['generated_at'])}</div>
  <div class="bar">
    <label>审题人：<input id="reviewer" placeholder="姓名/代号"></label>
    <label>日期：<input id="review-date" type="date"></label>
    <span id="progress">已审 0 / {len(worklist['questions'])}</span>
    <button class="ghost" onclick="jumpUnreviewed()">跳到未审</button>
    <button onclick="exportLabels()">导出标签 JSON</button>
  </div>
  <div class="muted">纪律：只看题面/锚点/答案位本身；<b>不要看跑批分数或判官输出</b>；中途关页会自动存草稿（本机），完成后导出 JSON <b>只发回负责人</b>。</div>
</header>
<main>{questions_html}</main>
<footer>okb F1 审题包 · 本页零网络、零上传；草稿只存在浏览器本机。<br>
判定口径见 README：✅无问题 / ⚠️题面歧义 / ❌答案或判据有误 / 🔀锚点与意图不符 / ❓不可判 / ✏️需改（其他）。</footer>
<script>{js}</script>
</body></html>
"""


README_TMPL = """# F1 人工审题包 · 使用说明

> 卷：`{paper}`（sha256 `{sha}`）· 判据：`{plan}`（sha256 `{plan_sha}`）· 共 {n} 题 · 生成：{date}
> 对应红线：F1 人工审题（freeze_status 六条之一）。本包**不代替** κ 标注与 §4.5 仪器复核。

## 一、这是什么

对交付卷**题目本身**的人工核验：题面是否有歧义、参考答案/判据是否正确、锚点是否在测它声称要测的东西。
**不评价 Agent 表现，不看跑批分数/判官输出**（防事后拟合）。

## 二、答案在哪里（两层，都要看）

1. **纸面答案位**（卡片右栏）：`verdict / confusions / gold_evidence / trap / reference_answer / notes`。
   注意：**卡派生题（75/100）的 `verdict=remembered` 是机械值**（卡片 `human_truth_label` 为 null），
   只表示"没有声明失败锚点"，**不是人工答案键**；
2. **计划判据**（卡片左栏"判据（四层计划）"区块）：**这才是这些题的实际评分依据**
   （例：`s8c_long_001` 的判据是"日报顺序=阻碍→已完成→明日计划"，而非纸面那两个结构锚点）。
   审题要点：
   - 判据 `requirement` 与题面能否互推；`on_failure` 覆盖了你认为最可能的错法吗；
   - **题面里的约束是否有判据承接**（如"不要发给任何人"却没有对应检查项 → 标发现项）；
   - "计划中没有该题检查项"或"纸面 confusions 与计划明显不一致" → 标发现项。

## 三、谁审、审多少

- 1-2 名真人，**非出题作者**；负责人参与需在报告披露（标记"开发核验"）。
- 最小版：抽审 20-30 道高危题（生成时加 `--sample-id ...`）；完整版：100 题全审（约 4-7 小时/人）。
- 两人版本：各自独立完成、不交流、不看对方结果；**两人读法不一致的题 = 强歧义信号**，单独列出后再仲裁。

## 四、怎么操作

1. 用 Chrome/Edge **本地**打开 `review-sheet.html`（离线；不需要与其它文件同目录）。
2. 填写审题人/日期；逐题按"题面 → 判据（计划）→ 锚点 → 答案位"顺序阅读，必要时看"机器提示"（anchorcal，仅供参考）。
3. 每题勾选：✅无问题 / ⚠️题面歧义 / ❌答案或判据有误 / 🔀锚点与意图不符 / ❓不可判 / ✏️需改（其他）。
4. 选严重度（必改/建议/仅记录），**标问题必须写理由**；必改写清"为什么 + 建议改法"。
5. 导出 `f1-labels-<姓名>.json`，**只发回负责人**（不发给另一审题人）。

## 五、回传与验收

```bash
# 结构/覆盖校验（题单绑定 sha、flag 合法、标问题必须有理由）
python tools/make_f1_review.py --check f1-labels-<姓名>.json \\
  --worklist handoff/f1-review-2026-10-09/review-worklist.json
```

- 汇总：负责人合并两份（如有）→ 必改项列清单交 B 进 v1.1（**改卷=新版本**；锚点类可对已有证据免费重评分；题面类需按题重跑或排除并公示）；
- 抽审 = **部分审阅**，不等于 F1 全绿；报告里如实写"审阅 N/100 + 发现项"；
- 审题意见**先收集、后批量实施**——不要在审题过程中直接改卷。

## 六、职责与披露（写进报告）

- 出题作者（B 线）**不能**作为本包审题人；
- 审题人是否参与过调参/看过判官输出，须在导出 JSON 里如实（无此字段则备注说明）；
- AI 代标的一律无效。
"""


def cmd_generate(args) -> int:
    paper = pathlib.Path(args.paper).resolve()
    if not paper.is_file():
        print(f"卷不存在：{paper}", file=sys.stderr)
        return 2
    suite, rows = load_any(str(paper))
    if args.sample_id:
        wanted = {s.strip() for s in args.sample_id.split(",") if s.strip()}
        rows = [r for r in rows if r.get("id") in wanted]
        missing = wanted - {r.get("id") for r in rows}
        if missing:
            print(f"--sample-id 不在卷内：{sorted(missing)}", file=sys.stderr)
            return 2
    sha = sha256_file(paper)
    hints = load_anchorcal(pathlib.Path(args.anchorcal)) if args.anchorcal else {}
    plan_path = pathlib.Path(args.plan).resolve() if args.plan else None
    plan_checks = load_plan(plan_path)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_at = args.date
    worklist = {
        "format": FORMAT,
        "labels_format": LABELS_FORMAT,
        "paper": str(paper.relative_to(ROOT)) if paper.is_relative_to(ROOT) else str(paper),
        "paper_sha256": sha,
        "suite_version": suite,
        "plan": (plan_path.relative_to(ROOT).as_posix() if plan_path and plan_path.is_relative_to(ROOT)
                 else (str(plan_path) if plan_path else None)),
        "plan_sha256": sha256_file(plan_path) if plan_path and plan_path.is_file() else None,
        "generated_at": generated_at,
        "generator": "tools/make_f1_review.py",
        "flags": list(FLAGS),
        "severities": list(SEVERITIES),
        "questions": [{"sample_id": r.get("id"), "dimension": r.get("dimension"),
                       "anchors": len(r.get("anchors") or []),
                       "plan_checks": [{"id": c.get("id"), "layer": c.get("layer"),
                                        "on_failure": c.get("on_failure"),
                                        "scope": c.get("scope")}
                                       for c in plan_checks.get(r.get("id"), [])]} for r in rows],
    }
    (out_dir / "review-worklist.json").write_text(
        json.dumps(worklist, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    cards = "\n".join(render_question(i, r, hints, plan_checks.get(r.get("id"), []))
                      for i, r in enumerate(rows, start=1))
    (out_dir / "review-sheet.html").write_text(build_html(worklist, cards),
                                               encoding="utf-8", newline="\n")
    readme = README_TMPL.format(
        paper=worklist["paper"], sha=sha, n=len(rows), date=generated_at,
        plan=worklist.get("plan") or "（未提供）",
        plan_sha=(worklist["plan_sha256"][:16] + "…") if worklist.get("plan_sha256") else "未提供")
    (out_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    print(f"审题包已生成：{out_dir}（{len(rows)} 题；paper sha {sha[:16]}…）")
    return 0


def cmd_check(args) -> int:
    labels_path = pathlib.Path(args.check)
    worklist_path = pathlib.Path(args.worklist)
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    problems: list[str] = []
    if labels.get("format") != LABELS_FORMAT:
        problems.append(f"labels.format 应为 {LABELS_FORMAT}")
    if labels.get("paper_sha256") != worklist.get("paper_sha256"):
        problems.append("paper_sha256 与题单不一致（回传件绑定了别的卷？）")
    if not labels.get("reviewer"):
        problems.append("缺 reviewer（审题人）")
    by_id = {q["sample_id"]: q for q in worklist.get("questions") or []}
    seen = set()
    for item in labels.get("items") or []:
        sid = item.get("sample_id")
        if sid not in by_id:
            problems.append(f"{sid}: 不在题单内")
            continue
        if sid in seen:
            problems.append(f"{sid}: 重复")
        seen.add(sid)
        flag = item.get("flag")
        if flag is not None and flag not in FLAGS:
            problems.append(f"{sid}: 非法 flag {flag!r}")
        sev = item.get("severity")
        if sev is not None and sev not in SEVERITIES:
            problems.append(f"{sid}: 非法 severity {sev!r}")
        if flag not in (None, "ok") and not (item.get("note") or "").strip():
            problems.append(f"{sid}: 标了问题但没有理由（note 为空）")
    print(f"校验完成：{len(seen)}/{len(by_id)} 题有回传；{len(problems)} 个问题")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F1 人工审题包生成器 / 回传校验")
    ap.add_argument("--paper", default=str(ROOT / "samples" / "paper-r1.yaml"))
    ap.add_argument("--plan", default=None,
                    help="四层计划（默认取 four-layer-r1-dev2.json，缺席退回 dev.1）—— "
                         "卡派生题的真正评分依据靠它呈现")
    ap.add_argument("--anchorcal", default=str(ROOT / "notes" / "anchorcal-paper-r1.json"),
                    help="可选：机器提示来源（不存在则跳过）")
    ap.add_argument("--out-dir")
    ap.add_argument("--sample-id", help="逗号分隔子集（抽审用）")
    ap.add_argument("--date", default="2026-10-09")
    ap.add_argument("--check", help="校验回传的 labels JSON（配合 --worklist）")
    ap.add_argument("--worklist", default=None)
    args = ap.parse_args(argv)
    if args.plan is None and not args.check:
        for cand in ("samples/four-layer-r1-dev2.json", "samples/four-layer-r1.json"):
            if (ROOT / cand).is_file():
                args.plan = str(ROOT / cand)
                break
    if args.check:
        if not args.worklist:
            ap.error("--check 需要 --worklist <review-worklist.json>")
        return cmd_check(args)
    if not args.out_dir:
        ap.error("生成模式需要 --out-dir")
    return cmd_generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
