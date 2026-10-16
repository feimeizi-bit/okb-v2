#!/usr/bin/env python3
"""检查项级「判官 vs 真人」离线审题单生成器（B 环自包含页）。

输入 annotate_checks 的公开工作单（worklist.jsonl），输出**单文件** review-sheet.html：
本地打开即可逐题三态标注（支持 / 违反 / 证据不足）+ 理由，自动存草稿，一键导出
labels-<标注者>-complete.jsonl —— 每行 row_key/label/note，可直接进
tools/annotate_checks.py validate / agreement，与私有 judge-keys.jsonl 对合。

纪律：
- 页面不含判官答案与评分侧元数据（result/rationale/citations/on_failure/evidence_tier
  一律不进页面）；题面只显示匿名题号 Q#，row_key 仅出现在导出件里；
- 判官答案在私有钥匙目录，双方交卷前不揭示。

用法：
    python tools/make_check_sheet.py \
        --worklist v2/03-review/check-blind-2026-10-14/worklist.jsonl \
        --out v2/03-review/check-blind-2026-10-14/review-sheet.html
    python tools/make_check_sheet.py --worklist … --out … \
        --prefill v2/03-review/check-blind-2026-10-14/AI-pre-labels-DRAFT-not-human.jsonl

--prefill 为"AI 预筛 + 人工裁决"复核模式：预勾选标签、预填理由并带"已核对"跟踪；
导出件携带 reviewed/prefill_sha256 字段，供汇总时如实披露（AI 草案不是人工标签）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import pathlib
import sys

FORMAT_SHEET = "okb-check-review-sheet-v1"
FORMAT_LABELS = "okb-check-labels-v1"
LABEL_VALUES = ("supported", "contradicted", "insufficient")
SCOPE_HINTS = {
    "memory_after": "看后态记忆条目：要求成立吗？",
    "agent_reply": "看回复文本：要求成立吗？",
    "file_final": "看最终文件：要求成立吗？",
}


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def render_sessions(task: dict) -> str:
    parts = []
    for s in task.get("script") or []:
        head = "session=%s" % esc(s.get("session", "?"))
        if s.get("time"):
            head += " · 时间 %s" % esc(s["time"])
        turns = "".join("<li><span class='turn'>t%d</span>%s</li>" % (i, esc(t.get("text", "")))
                        for i, t in enumerate(s.get("turns") or [], 1))
        parts.append("<div class='session'><div class='sess-head'>%s</div><ol>%s</ol></div>"
                     % (head, turns))
    return "".join(parts) or "<div class='muted'>（无脚本）</div>"


def render_files(task: dict) -> str:
    files = task.get("initial_files") or []
    if not files:
        return "<div class='muted'>（无初始文件）</div>"
    return "".join("<details class='file'><summary>%s</summary><pre>%s</pre></details>"
                   % (esc(f.get("path", "?")), esc(f.get("content", ""))) for f in files)


def render_obs(obs: dict) -> str:
    eid = esc(obs.get("evidence_id", "?"))
    c = obs.get("content")
    quick = ""
    if isinstance(c, dict):
        if "path" in c and ("after_content" in c or "op" in c):
            quick = "<div class='obs-head'>path=%s · op=%s</div>" % (
                esc(c.get("path")), esc(c.get("op", "")))
            if c.get("before_content") is not None:
                quick += "<div class='muted'>变更前</div><pre>%s</pre>" % esc(c["before_content"])
            quick += "<div class='muted'>变更后</div><pre>%s</pre>" % esc(c.get("after_content"))
        elif "path" in c and "exists" in c:
            quick = "<div class='obs-head'>path=%s · exists=%s · origin=%s</div>" % (
                esc(c.get("path")), esc(c.get("exists")), esc(c.get("origin", "")))
            if c.get("content") is not None:
                quick += "<pre>%s</pre>" % esc(c.get("content"))
        elif set(c) == {"text"}:
            quick = "<pre>%s</pre>" % esc(c["text"])
        elif "entries" in c:
            rows = []
            for e in c.get("entries") or []:
                body = e.get("value", e.get("key", ""))
                meta = " · ".join(str(x) for x in (e.get("written_at"), e.get("written_at_source"))
                                  if x)
                rows.append("<li><pre>%s</pre><span class='muted'>%s</span></li>"
                            % (esc(body), esc(meta)))
            quick = ("<div class='obs-head'>记忆条目 %s 条 · phase=%s · via=%s</div><ol>%s</ol>"
                     % (esc(c.get("entries_count")), esc(c.get("phase")), esc(c.get("via")),
                        "".join(rows)))
    raw = json.dumps(c, ensure_ascii=False, indent=2)
    return ("<div class='obs'><div class='obs-eid'>%s</div>%s"
            "<details><summary>原始 JSON</summary><pre>%s</pre></details></div>"
            % (eid, quick, esc(raw)))


def render_item(no: int, item: dict, prefill: dict | None = None) -> str:
    body = item["item"]
    task = body.get("task") or {}
    scope = body.get("scope") or "?"
    req = body.get("requirement") or ""
    chips = ["scope=%s" % esc(scope)]
    if task.get("dimension"):
        chips.append("dimension=%s" % esc(task["dimension"]))
    for key in ("boundary_kind", "time_anchor"):
        if task.get(key):
            chips.append("%s=%s" % (key, esc(task[key])))
    hint = SCOPE_HINTS.get(scope, "按判据要求核对观察证据。")
    obs = "".join(render_obs(o) for o in body.get("observations") or [])
    raw_task = json.dumps(task, ensure_ascii=False, indent=2)
    pre = prefill or {}

    def radio(value: str, text: str) -> str:
        chk = " checked" if pre.get("label") == value else ""
        return ("<label class='opt'><input type='radio' name='label-Q%d' value='%s'%s>%s</label>"
                % (no, value, chk, text))

    radios = radio("supported", "✅ 支持") + radio("contradicted", "❌ 违反") \
        + radio("insufficient", "❓ 证据不足")
    tag = "<span class='ai-tag'>AI 草案·待裁决</span>" if prefill else ""
    reviewed = ("<label class='opt'><input type='checkbox' id='rev-Q%d'> 已核对</label>" % no) \
        if prefill else ""
    note = esc(pre.get("note", ""))
    return """
<section class="q" id="q-%(no)d">
  <div class="q-head"><span class="q-no">Q%(no)d</span>
    <span class="muted">%(chips)s</span>
    %(tag)s
    <span class="state" id="state-Q%(no)d">未标</span></div>
  <div class="req"><b>判据要求：</b>%(req)s</div>
  <div class="muted">%(hint)s</div>
  <div class="cols">
    <div class="col">
      <h4>脚本（用户可见的话）</h4>%(sessions)s
      <h4>初始文件</h4>%(files)s
      <details><summary>task 原始 JSON</summary><pre>%(raw_task)s</pre></details>
    </div>
    <div class="col">
      <h4>观察证据（判官看到的材料）</h4>%(obs)s
    </div>
  </div>
  <div class="review">
    <div class="radios">%(radios)s%(reviewed)s</div>
    <textarea id="note-Q%(no)d" rows="2"
      placeholder="理由（必填，≥8 字）：引用证据编号 E#，写清为什么">%(note)s</textarea>
  </div>
</section>""" % dict(no=no, chips=" · ".join(chips), req=esc(req), hint=esc(hint),
                     sessions=render_sessions(task), files=render_files(task),
                     raw_task=esc(raw_task), obs=obs, radios=radios, tag=tag,
                     reviewed=reviewed, note=note)


def build_html(worklist_path: pathlib.Path, title: str, generated_at: str,
               prefill_path: pathlib.Path | None = None) -> tuple[str, dict]:
    rows = read_jsonl(worklist_path)
    head = rows[0]
    if head.get("format") != "okb-check-worklist-v1":
        raise ValueError("not a check worklist: " + str(worklist_path))
    items = rows[1:]
    worklist_sha = sha256_file(worklist_path)
    prefill: dict[str, dict] = {}
    prefill_sha = None
    if prefill_path is not None:
        prefill = {r["row_key"]: r for r in read_jsonl(prefill_path)}
        prefill_sha = sha256_file(prefill_path)
        if len(prefill) != len(items) or set(prefill) != {it["row_key"] for it in items}:
            raise ValueError("prefill must cover exactly the worklist row_keys")
    body = "".join(render_item(i, it, prefill.get(it["row_key"]))
                   for i, it in enumerate(items, 1))
    payload = dict(format=FORMAT_SHEET, labels_format=FORMAT_LABELS, title=title,
                   generated_at=generated_at, generator="tools/make_check_sheet.py",
                   worklist_sha256=worklist_sha, prefill_sha256=prefill_sha, worklist=head,
                   items=[dict(no=i, row_key=it["row_key"], scope=it["item"].get("scope"),
                               item_sha256=it["item_sha256"],
                               requirement=it["item"].get("requirement"),
                               task=it["item"].get("task"),
                               observations=it["item"].get("observations"))
                          for i, it in enumerate(items, 1)])
    sheet_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    source = esc(head.get("source_package", ""))
    banner = ""
    if prefill_sha:
        banner = ("<div class=\"banner\"><b>本页已预填 AI 预筛草案</b>（AI 与判官互相独立、"
                  "均不可见对方）：请逐条核对——同意即勾『已核对』，不同意直接改标签或理由；"
                  "导出件将披露为『AI 预筛 + 人工裁决』。</div>")
    page = TEMPLATE.replace("__TITLE__", esc(title)) \
                   .replace("__SOURCE__", source) \
                   .replace("__WL_SHA__", worklist_sha[:16]) \
                   .replace("__COUNT__", str(len(items))) \
                   .replace("__GENERATED__", esc(generated_at)) \
                   .replace("__TODAY__", esc(generated_at)) \
                   .replace("__PREFILL_BANNER__", banner) \
                   .replace("__ITEMS__", body) \
                   .replace("__SHEET_JSON__", sheet_json)
    return page, payload


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;background:#f5f6f8;color:#1c1e21}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #d8dade;padding:10px 16px;z-index:9}
h1{font-size:18px;margin:4px 0}
.muted{color:#6b7280;font-size:12px}
.bar{margin-top:6px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:13px}
button{padding:6px 12px;border:1px solid #2563eb;background:#2563eb;color:#fff;border-radius:6px;cursor:pointer}
button.ghost{background:#fff;color:#2563eb}
main{max-width:1180px;margin:0 auto;padding:14px}
.q{background:#fff;border:1px solid #e2e4e8;border-radius:10px;margin:14px 0;padding:12px 16px}
.q-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.q-no{color:#9ca3af}
.state{margin-left:auto;font-size:12px;color:#6b7280}
.state.done{color:#059669}
.state.part{color:#d97706}
.req{background:#f8fbff;border:1px solid #dbeafe;border-radius:6px;padding:6px 10px;margin:8px 0;font-size:13px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
h4{margin:10px 0 6px;font-size:13px;color:#374151}
.session{margin:6px 0;border-left:3px solid #e5e7eb;padding-left:8px}
.sess-head{font-size:12px;color:#6b7280}
.turn{display:inline-block;min-width:26px;color:#9ca3af;font-size:12px}
ol{margin:4px 0 4px 18px;padding:0}
pre{background:#f8f9fa;border:1px solid #eceef1;border-radius:6px;padding:6px 8px;font-size:12px;
    white-space:pre-wrap;word-break:break-word;margin:4px 0}
.obs{border:1px dashed #e5e7eb;border-radius:6px;padding:6px 8px;margin:6px 0;font-size:13px}
.obs-eid{font-weight:600;color:#2563eb;font-size:12px}
.obs-head{font-size:12px;color:#374151;margin-top:2px}
.file{margin:4px 0}
details>summary{cursor:pointer;font-size:12px;color:#2563eb}
.review{margin-top:10px;border-top:1px solid #f0f1f3;padding-top:8px}
.opt{margin-right:14px;font-size:13px}
textarea{width:100%;box-sizing:border-box;border:1px solid #d1d5db;border-radius:6px;padding:6px;font-size:13px}
.expo{font-size:12px;color:#374151;margin-right:16px}
.ai-tag{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;font-size:11px;
        padding:1px 8px;border-radius:999px}
.banner{background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:6px 10px;
        font-size:12px;margin-top:6px}
footer{max-width:1180px;margin:0 auto;padding:10px 14px 40px;color:#6b7280;font-size:12px}
</style></head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="muted">来源：<code>__SOURCE__</code> · 工作单 sha256 <code>__WL_SHA__…</code>
    · 共 __COUNT__ 题 · 生成于 __GENERATED__</div>
  <div class="bar">
    <label>标注者：<input id="reviewer" placeholder="姓名/代号"></label>
    <label>日期：<input id="review-date" type="date" value="__TODAY__"></label>
    <span id="progress">已标 0 / __COUNT__</span>
    <button class="ghost" onclick="jumpUnlabeled()">跳到未标</button>
    <button onclick="exportLabels()">导出标签 JSONL</button>
  </div>
  <div>
    <label class="expo"><input type="checkbox" id="expo-machine"> 我看过本批机器结果/判官输出（按“开发核验”单列）</label>
    <label class="expo"><input type="checkbox" id="expo-other"> 我看过另一位标注者的标签</label>
  </div>
  <div class="muted">纪律：只按“判据要求”核对“观察证据”；本页不含判官答案；不要与另一位标注者交换草稿；
    <b>理由必填（≥8 字，引用 E#）</b>；草稿自动存本机浏览器；完成后导出 JSONL <b>只发回负责人</b>。</div>
  __PREFILL_BANNER__
</header>
<main>__ITEMS__</main>
<footer>okb 检查项盲审包 · 本页零网络、零上传；草稿只存在浏览器本机。<br>
三态口径：✅支持（证据支持要求成立） / ❌违反（有足够证据证明要求不成立） / ❓证据不足（范围不全、冲突或对象时间不清）。<br>
交回格式 labels-&lt;标注者&gt;-complete.jsonl，与私有判官钥匙对合后再揭示一致率。</footer>
<script>
const SHEET = __SHEET_JSON__;
const draftKey = 'okb-check-sheet-' + SHEET.worklist_sha256 + '-' +
                 (SHEET.prefill_sha256 ? SHEET.prefill_sha256.slice(0, 8) : 'blank');
const refs = SHEET.items.map(it => ({no: it.no, row_key: it.row_key, revId: 'rev-Q' + it.no,
  radioName: 'label-Q' + it.no, noteId: 'note-Q' + it.no, stateId: 'state-Q' + it.no}));
function itemState(ref) {
  const label = document.querySelector('input[name="' + ref.radioName + '"]:checked');
  const note = document.getElementById(ref.noteId).value.trim();
  return {label: label ? label.value : null, noteOk: note.length >= 8};
}
const LABEL_TEXT = {supported: '✅ 支持', contradicted: '❌ 违反', insufficient: '❓ 不足'};
function refreshStates() {
  let done = 0, picked = 0, reviewed = 0, revTracked = 0;
  const hist = {supported: 0, contradicted: 0, insufficient: 0};
  for (const ref of refs) {
    const st = itemState(ref);
    const el = document.getElementById(ref.stateId);
    const rc = document.getElementById(ref.revId);
    if (rc) { revTracked++; if (rc.checked) reviewed++; }
    if (st.label) picked++;
    if (st.label && st.noteOk) { done++; hist[st.label]++; el.textContent = LABEL_TEXT[st.label];
      el.className = 'state done'; }
    else if (st.label) { el.textContent = LABEL_TEXT[st.label] + '（缺理由）'; el.className = 'state part'; }
    else { el.textContent = '未标'; el.className = 'state'; }
  }
  document.getElementById('progress').textContent =
    (revTracked ? '已核对 ' + reviewed + ' / ' + refs.length + ' · ' : '')
    + '已标 ' + done + ' / ' + refs.length + '（支持 ' + hist.supported + ' · 违反 '
    + hist.contradicted + ' · 不足 ' + hist.insufficient + (picked - done > 0 ?
    ' · 待补理由 ' + (picked - done) : '') + '）';
}
function collect() {
  const reviewer = document.getElementById('reviewer').value.trim();
  const date = document.getElementById('review-date').value;
  const exposure = {machine_results_seen: document.getElementById('expo-machine').checked,
                    other_rater_seen: document.getElementById('expo-other').checked};
  return refs.map(ref => {
    const rc = document.getElementById(ref.revId);
    return {format: SHEET.labels_format, row_key: ref.row_key,
      label: itemState(ref).label, note: document.getElementById(ref.noteId).value.trim(),
      reviewer: reviewer, date: date, exposure: exposure,
      reviewed: rc ? rc.checked : null, prefill_sha256: SHEET.prefill_sha256 || null,
      worklist_sha256: SHEET.worklist_sha256, export_state: 'complete'};
  });
}
function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], {type: 'application/x-ndjson;charset=utf-8'}));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
}
function exportLabels() {
  const rows = collect();
  const bad = rows.filter(r => !r.label || r.note.length < 8).length;
  const unreviewed = rows.filter(r => r.reviewed === false).length;
  const problems = [];
  if (bad) problems.push('还有 ' + bad + ' 条未完成（缺标签或理由少于 8 字）');
  if (unreviewed) problems.push('还有 ' + unreviewed + ' 条未勾选『已核对』');
  if (problems.length && !confirm(problems.join('；') + '——仍要导出？')) return;
  const name = 'labels-' + ((rows[0] && rows[0].reviewer) || 'anon') + '-' +
               (problems.length ? 'draft' : 'complete') + '.jsonl';
  download(name, rows.map(r => JSON.stringify(r)).join('\\n') + '\\n');
}
function jumpUnlabeled() {
  for (const ref of refs) { const st = itemState(ref);
    if (!st.label || !st.noteOk) { document.getElementById('q-' + ref.no)
      .scrollIntoView({behavior: 'smooth', block: 'center'}); return; } }
}
function saveDraft() { try { localStorage.setItem(draftKey, JSON.stringify(collect())); } catch (e) {} }
function loadDraft() {
  try {
    const rows = JSON.parse(localStorage.getItem(draftKey) || 'null');
    if (!rows) return;
    for (const row of rows) {
      const ref = refs.find(r => r.row_key === row.row_key);
      if (!ref) continue;
      if (row.label) { const el = document.querySelector('input[name="' + ref.radioName +
        '"][value="' + row.label + '"]'); if (el) el.checked = true; }
      if (row.note) document.getElementById(ref.noteId).value = row.note;
      const rc = document.getElementById(ref.revId);
      if (rc && row.reviewed != null) rc.checked = row.reviewed;
    }
    if (rows[0]) {
      document.getElementById('reviewer').value = rows[0].reviewer || '';
      if (rows[0].date) document.getElementById('review-date').value = rows[0].date;
      const ex = rows[0].exposure || {};
      document.getElementById('expo-machine').checked = !!ex.machine_results_seen;
      document.getElementById('expo-other').checked = !!ex.other_rater_seen;
    }
  } catch (e) {}
}
document.addEventListener('input', function () { refreshStates(); saveDraft(); });
document.addEventListener('change', function () { refreshStates(); saveDraft(); });
window.addEventListener('beforeunload', saveDraft);
loadDraft(); refreshStates();
</script>
</body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成检查项盲审自包含审题单（单文件 HTML）")
    ap.add_argument("--worklist", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="okb · 检查项盲审单（判官校准）")
    ap.add_argument("--generated-at", default=None, help="默认今天（YYYY-MM-DD）")
    ap.add_argument("--prefill", default=None, help="AI 预筛草案 JSONL（复核模式，可选）")
    args = ap.parse_args(argv)
    generated_at = args.generated_at or datetime.date.today().isoformat()
    prefill_path = pathlib.Path(args.prefill).resolve() if args.prefill else None
    page, payload = build_html(pathlib.Path(args.worklist).resolve(), args.title,
                               generated_at, prefill_path)
    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8", newline="\n")
    print(json.dumps(dict(sheet=str(out), items=len(payload["items"]),
                          worklist_sha256=payload["worklist_sha256"],
                          prefill_sha256=payload["prefill_sha256"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
