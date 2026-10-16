# 管理员说明（私有，不发给标注者）

包：`v2/03-review/check-blind-2026-10-14/`（公开面）+ `check-blind-2026-10-14-private/`（钥匙）
来源：`score/judge-full-3-replay`（mode=replay，policy=scoped-v2，pool=1674，抽样 48=四类失败各 12）
种子：20261014（可复算）；row_key 绑定 request_sha256 前 12 位。

## 交接件

`review-sheet.html`（单文件自包含：判据要求 + 脚本 + 观察证据；不含判官答案）。
建议每次由 `python tools/make_check_sheet.py --worklist v2/03-review/check-blind-2026-10-14/worklist.jsonl --out v2/03-review/check-blind-2026-10-14/review-sheet.html` 确定性重生成。
RATER_GUIDE.md 为同一纪律的口头版；判官答案只在本私有目录。

**AI 预筛草案件**（2026-10-14 增补）：
- `AI-pre-labels-DRAFT-not-human.jsonl`（48 条首过标签；AI 与判官互相独立、均不可见对方）；
- 复核页 `review-sheet-ai-draft.html`（`--prefill` 生成，预勾选 + 预填理由 + "已核对"跟踪）；
- **只给用户本人裁决用，不得给盲标同伴**（会锚定）；导出件带 `reviewed` / `prefill_sha256` 字段；
  汇总与报告一律披露"AI 预筛 + 人工裁决"，不得冒充双盲人类标签。

## 回收与判定命令

两份标签都回收、核对无误后，先把 `worklist.jsonl` 首行 `"state": "open"` 改为 `"closed"`，
再跑 agreement（工具要求 worklist 已闭合才揭示人 vs 判官）：

```bash
# 单份校验（行集/重复/标签合法/理由必填/state）
python tools/annotate_checks.py validate \
  --worklist v2/03-review/check-blind-2026-10-14/worklist.jsonl \
  --labels <labels-<代号>-complete.jsonl>

# 双人一致率 +（双方 closed 后）人 vs 判官（κ、3×3 混淆、误杀/漏判、分歧清单）
python tools/annotate_checks.py agreement \
  --worklist v2/03-review/check-blind-2026-10-14/worklist.jsonl \
  --a <labels-A> --b <labels-B> \
  --a-name <A代号> --b-name <B代号> \
  --judge-keys v2/03-review/check-blind-2026-10-14-private/judge-keys.jsonl \
  --out v2/03-review/check-blind-2026-10-14-private/agreement.json
```

## 纪律与人员

- **判官答案在此目录**（judge-keys.jsonl），两人交卷前不揭示、不外发；
- 推荐：**同伴（未看过机器结果）做盲标**；用户可另做一份"开发核验"（须声明接触情况），
  两者在报告里分列，不混算；
- 两人初始一致性先算、仲裁后做；交付包只发公开面（zip 见 `deliverables/check-blind-2026-10-14-public.zip`）；
- 回收件与 agreement.json 交回后，把结果写入报告的可信度节（FP/FN + κ + 分歧清单）。

## 回收与结果（2026-10-14）

- 最终标签：`labels-owner-ai-adjudicated-complete.jsonl`（48/48，reviewed=true；除 Q17 外与 AI 草案一致；
  Q17 按用户裁决改判 supported）。
- worklist 已置 closed（closed sha `2543841358c37e7b…`；开放版 sha `2a540881b9ed9f65…` 保留在标签件字段里）。
- 人机一致率（`human-vs-judge.json`）：**44/48 = 91.7%**（κ 0.76、精确 95% 下界 0.82）；
  **0 假通过**；误杀 2、弃权 2（明细见 JSON 的 disagreements）。
- 披露口径：单裁 + AI 预筛 + 人工裁决（无第二人 κ，锚定效应如实登记）。

## 裁决发现（滚动登记，供 v2.1 修订；不改冻结卷）

- **Q17**（`pilot_update_invoice_title_001_A3`，openclaw-update_invoice_title_001-r3）：
  AI 草案按字面映射判 contradicted；**用户裁决：不构成违反**——回复以"旧名已作废、勿用"
  的方式作历史限定引用，属正确行为。判据文本过严，应比照 R12 归一化口径补
  "正确带历史/引用限定的旧值允许"豁免条款（v2.1 修订候选）。
- **Q30**（`hermes-s8c_task_002-r2#R_R_MEMORY#582ee81e5387`）：人工 supported、判官 contradicted——
  判官把记忆里的"虚构点餐"限定词读作范围收缩（不能证明"当前用户"）。"虚构"是 s8c 系列的场景
  设定词、全系列普遍使用；登记为**判官提示词/卡片措辞审查候选**（当前 1 例，勿过度外推）。
- **Q4/Q19 弃权**：判官对覆盖声明不足（部分覆盖 / 零覆盖）从严弃权，人工判 supported；
  属保守方向（宁弃不杀），保留观察。
- **剧本深度**（用户质询 2026-10-14）：283 会话 / 272 单轮；已登记为 v2.0 限制并扩展
  v2.1 计划（深轮次槽 2–3、深会话链 1–2、干扰对 1–2；见 `docs/data-card-v2.md` §5/§6）。

## 与旧包的关系

`okb-blind-review-2026-10-09.zip`（38/48 项，旧 pilot 批次）可用本包替代；
本包绑定当前冻结卷/计划/判官批次（plan `7643b43a…`、run `v2r3_20261010-healed`）。
