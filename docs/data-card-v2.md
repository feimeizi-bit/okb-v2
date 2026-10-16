# okb-v2 数据卡（冻结版 v2.0-R12）

> 状态：**赛题线冻结**；维护模式。日期：2026-10-14。
> 用途：任何引用本项目数字的人（评委/面试官/未来的自己）先读这一页——
> 这里定义每个数字的口径、来源与边界；细节查询用《白板三图-答案卡》，
> 项目全貌用《项目心智模型》，模块深挖按需。
> 一句话：**本卡全部数字为"探索性诊断"，不是正式成绩**（原因见 §5）。

## 1. 冻结清单（每个数字都可追到这些文件）

> **字节约定（2026-10-14 冻结）**：仓内 `.gitattributes = * -text`、`core.autocrlf=false`，
> 全部文本文件为 **LF**；唯一例外 `paper-v2.plan.json` 为 **CRLF**（判官记录绑定其 sha，
> 改动即全部记录不可回放）。下表 sha 为冻结后的实际字节。
> 事故记录：`core.autocrlf=true` 的检出曾把 1.1 万文件转成 CRLF 导致重放失败；
> 已逐文件还原为锁定字节（13101/13101 与 input-lock 一致）并加字节冻结防复发。

| 资产 | 路径 | sha256（前 16） |
|---|---|---|
| 正式卷（92 题） | `v2/01-paper-v2/_paperv2.jsonl` | `bca2c68c82be510f` |
| 四层计划（330 检查，R12 后） | `v2/01-paper-v2/paper-v2.plan.json` | `7643b43a83aab4cb`（CRLF） |
| 判词归一化台账（94 条） | `v2/01-paper-v2/plan-requirement-normalization.json` | `7d696f3367492efc` |
| 断言库 registry（629 槽位） | `v2/05-assertions/registry-v2.json` | `8c622737d4b677f5` |
| 真机证据（552 任务） | `runs/v2r3_20261010-healed/` | manifest `e2c2dde93c86d588`；evidence `3852ba1767984ba9` |
| 判官批次 3 记录 | `score/judge-full-3/records.jsonl` | `6e5d68e66d390c02` |
| 判官评分结果（回放） | `score/judge-full-3-replay/results.json` | `afc8b73a26cba085` |
| 判官批次 4（A/A）记录 | `score/judge-full-4/records.jsonl` | `6acf7cd21bb969b5` |
| 诊断报告 + 雷达 | `score/final-report-r12/` | report `9a9d88d6a65526a6`；radar `b6a5d2798f130cc5` |
| 难度/区分度台账 | `v2/06-qc/item-stats-v2.json` | `0d3a6d347f214c5b` |
| 双口径 + 检查族 | `v2/06-qc/metric-bounds-v2.json` | `45f2d0232e451c8b` |
| A/A 翻转率报告 | `v2/06-qc/judge-aa-v2.json` | `3ce35fe2e69bebef` |
| 质检报告（红线 1-5） | `v2/06-qc/qc-v2-report.md` | `8a4962f6d4404e35` |
| openKylin 复现回执 | `deliverables/c-openkylin-verify/receipt.md` | 含链/评分/deb 现场记录 |

判官：`gpt-6.1-sol`（reasoning_effort=low）；评分口径：`scoped-v2` + 四层；卷：92 题（存量修复 68 + 新著 24）。
**openKylin 跨机验证**：同字节输入下，VM 与宿主评分重放 results 逐字节一致（`afc8b73a…`）。

## 2. 结果总表（口径先读这里）

**两种口径必须并列**：
- **口径 A（可判口径）**：通过率 = 通过 ÷ (通过+失败)，不可判剔除（雷达画这个）；
- **口径 B（下界口径）**：通过率 = 通过 ÷ 全部计划任务，不可判按失败计。

| Agent | 计划 | 通过/失败/不可判 | 口径 A | 口径 B | 覆盖率 |
|---|---|---|---|---|---|
| hermes | 276 | 194 / 43 / 39 | **0.819** | **0.703** | 0.859 |
| openclaw | 276 | 132 / 116 / 28 | **0.532** | **0.478** | 0.899 |

**分维度**（A / B / 覆盖率；标注 ⚠ 的为样本覆盖率 <80%、按规格只进雷达不进总分）：

| 维度 | hermes A / B / 覆盖 | openclaw A / B / 覆盖 |
|---|---|---|
| 长期保持 | 1.000 / 0.867 / 0.867 | 0.474 / 0.400 / 0.844 |
| 记忆调用 | 0.902 / 0.822 / 0.911 | 0.689 / 0.689 / 1.000 |
| 动态更新 ⚠ | 0.675 / 0.600 / 0.889（样本覆盖 0.73） | 0.366 / 0.333 / 0.911 |
| 相近区分 | 0.932 / 0.911 / 0.978 | 0.810 / 0.756 / 0.933 |
| 边界识别 | 0.800 / 0.711 / 0.889 | 0.511 / 0.511 / 1.000 |
| 任务复用 ⚠ | 0.545 / 0.353 / 0.647（样本覆盖 0.59） | 0.297 / 0.216 / 0.726 |

**注意**：hermes 长期保持 1.000 的 95% Wilson 区间为 **[0.91, 1.00]**（39/39，6 个不可判被剔除），
称"≥91%"比称"满分"诚实；该维度区分度 0.53（全卷最高），对 openclaw 仍在测量。

## 3. 质量校准证据（真机）

- **item 台账**（92 题）：高区分（|Δ|≥0.6）**23** · 饱和（双 Agent 全过）**30** · 地板（双挂）**9** · 重复波动 **30**。
  分维度平均难度/区分度：长期保持 .737/.526 · 记忆调用 .767/.289 · 动态更新 .511/.381 ·
  相近区分 .878/.119 · 边界识别 .622/.238 · 任务复用 .399/.346。
- **判官稳定性（A/A）**：同计划同请求两轮，请求级翻转 **21/1674 = 1.25%**
  （决定性 supported↔contradicted 仅 **2 条 = 0.12%**），任务级 12/552 = 2.17% → 单批结论可引用。
- **人机校准（单裁 48 例，2026-10-14）**：检查项级分层盲审（四类失败面各 12，抽自 1674 池；
  AI 预筛 + 人工裁决）：人机一致 **44/48 = 91.7%**（κ 0.76，精确 95% 下界 0.82），
  **0 假通过**、2 误杀（判据/语义过严各 1）、2 弃权（覆盖声明不足）；
  包与结果：`v2/03-review/check-blind-2026-10-14/`（判官面在私有 `human-vs-judge.json`）。
- **判词极性修复（R12，2026-10-13）**：94 条 requirement 归一化为「合规命题+显式映射」；
  修复前审计确认 17 个任务被反判判死（11 应 pass / 6 应 unassessable）、2 个假通过；
  全量重判验收：wifi 检查 6/6 翻转正确、17 个任务全部纠正。防线：`tools/plan_lint.py` E201/E202（阻断级）。
- **工程回归**：`tools/tests`+`engine/tests` 收尾合计 **921 passed / 63 skipped / 0 failed**
  （冻结时 914；新增 7 项为 B 环复核工具测试）；
  复算链连续重跑 54 个产物 0 漂移；样本 lint 0 error / 61 warning（W205×52 注记、W207×9 豁免）。

## 4. 赛题"多种记忆情形"覆盖矩阵

| 赛题点名 | 代表题 | 数量 | 评级 |
|---|---|---|---|
| 稳定偏好 | retain_allergy / retain_nickname / similar_drink / recall_coffee 等 | ≥10 | ✅ 充足 |
| 路径/路线 | update_route / update_delayed / v2_du_delay_address / similar_campus / v2_du_delay_approval | 5 | ✅ 基本 |
| 模板/格式 | reuse_report / v2_du_version_rollback / v2_tr_two_artifact_sections / v2_du_propagate | ≥6 | ✅ 充足 |
| 动态更新/冲突 | dynamic_update 全维（延迟生效×3、回退、条件、跨会话链、相邻字段、重复更正、双产物传播） | 15 | ✅ 最丰富 |
| 相近区分 | WiFi/联系人/校区/会议/发票/工位/饮品 | 15 | ✅ 充足 |
| 临时信息 | boundary_temp_001 / boundary_temp_path_001 / s8c_boundary_004/011/013 | ≥4 | ✅ 基本 |
| 敏感信息 | boundary_pii_mask_001 / v2_br_withhold_phone_001 / v2_br_withhold_otp_001 / update_multi_001 | 4 | ⚠️ 偏薄 |
| 风险指令 | boundary_risk_001 / s8c_boundary_012 / boundary_temp_path_001 | 3 | ✅ 基本 |
| 噪声/干扰 | 全卷 disturb 会话；强扰动 4 题；干扰值题 v2_mr_decoy_workhours / 多跳 v2_mr_multihop_pickup | — | ✅ 充足 |

**证据面**：对话 ✅ / 记忆 ✅ / 文件 ✅ 全卷；**工具面 1 题**（reuse_report tool_call 锚点）；
**引擎时间锚 1 题**（update_delayed；另 3 题靠内容日期）→ 证据面约 3.5/5。

## 5. 已知限制（为什么不是正式成绩）

1. **人审样本少且非双盲**：仅 1 份"AI 预筛 + 人工裁决"单裁件（48 例分层样本：人机一致 91.7%、
   κ 0.76、0 假通过、2 误杀 2 弃权）；无第二人 κ（AI 草案存在锚定效应），绝对分仍按
   "探索性诊断"披露；正式成绩路线图见 §6（双人人审 ≥50 例）。
2. **覆盖率门槛**：按规格（可测样本 ≥10 且样本覆盖率 ≥80%），hermes 动态更新（0.73）、
   任务复用（0.59）与 openclaw 任务复用（0.65）不达标 → 只进雷达、不进正式总分。
3. **记忆面可比性未证**：T2 仅是回执绑定，非带外验证；存在"回复/产物正确但记忆面检查挂"的
   任务（openclaw 9 / hermes 6），采集面偏差待探针实验。
4. **失败归因粒度**：记忆应用只能在回复/产物面观测，"记了没用对"与"没记住"在五分类不可分；
   `task-application` 族含少量非记忆质量检查（`A1_content_CONF` 11 条）待逐条审计。
5. **时间纵深薄**：长时程（跨日历月）实质样本少；是长期保持对强 Agent 饱和的主因。
6. **敏感/工具样本薄**：PII 声明 2 题（敏感类合计 4）；工具行动面 1 题。
7. **剧本深度浅**：92 题共 283 个会话，其中 272 个仅 1 轮（96%）；题内总轮数中位 3、最大 6，
   仅 2 题存在 >2 轮的会话；干扰会话同为单轮。本套测的是**跨会话持久化与更新冲突**
   （引擎逐会话清场，不靠轮数），**不构成对长对话上下文（漂移/中段遗忘/远距离指代）的
   压力测试**；深轮次槽列入 §6。

## 6. v2.1 扩展计划（不执行，仅登记）

- 时间跨度：新写或改造 2 题（跨 ≥2 日历月 + 生效窗口 / 跨任务二次复用），补"时间 3"深化槽；
- 敏感信息：补 2–3 题 withhold（打码/部分持久化/变形形态）；
- 深轮次槽：2–3 题单会话 12–20 轮（关键信息埋 t1–3，探测 ≥12 轮后，中段 8–10 轮无关话题）；
- 深会话链：1–2 题 6–8 个会话（写 → 多段干扰 → 更新 → 延迟 2–3 个会话再探测）；
- 干扰对：1–2 题两条相似主题并行更新（同名字段不同对象），压力干扰累积；
- 构念审计：对 `A1_content_CONF` 11 条逐条标注"记忆应用 / 产物质量 / 风格"并决定去留；
- 记忆面探针：1 题 ×2 Agent ×2–3 轮，验证"采不到≠没记住"是否成立；
- 人审：≥50 例（六维×双 Agent 均衡）→ κ，然后才谈"正式成绩"。
- 流程：同一条复算链（改动题 → 重采受影响题 → 全量重判 → 报告），卷版本号 v2.1；
  采集/判官调用需预算口令后再执行。

## 7. 复现与校验（全离线；判官重跑需预算口令）

```bash
# 复算链（应输出 sha bca2c68c、94 rewrites、plan-lint 0E）
python v2/02-generators/build_stock_v2.py
python v2/02-generators/assemble_v2.py
python v2/02-generators/build_registry.py && python v2/02-generators/qc_v2.py

# 校验
python tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json        # 0 error
python tools/validate.py v2/01-paper-v2/paper-v2.yaml                     # 0E/61W
python -m pytest tools/tests engine/tests -q                              # 921 passed
python tools/item_stats.py --results score/judge-full-3-replay/results.json \
  --plan v2/01-paper-v2/paper-v2.plan.json --paper v2/01-paper-v2/_paperv2.jsonl \
  --run-id v2r3_20261010 --out-dir v2/06-qc
python tools/metric_bounds.py --report score/final-report-r12/report.json --out-dir v2/06-qc
python tools/judge_aa.py --records-a score/judge-full-3/records.jsonl \
  --records-b score/judge-full-4/records.jsonl --requests score/host-prepare-r12/semantic-requests.json \
  --results-a score/judge-full-3-replay/results.json --results-b score/judge-full-4-replay/results.json \
  --out-dir v2/06-qc
```

## 8. 维护规则（改什么 → 触发什么）

| 改动 | 必须重跑 | 备注 |
|---|---|---|
| 改判词/检查文本 | plan-lint → **全量重判**（plan sha 变，旧记录不可回放） | 必须先过 E201/E202 |
| 改卷（题面/fixture/锚点） | 卷版本 +1；**只重采受影响题**；然后全量重判 | 旧证据仅改动题作废 |
| 改引擎采集口径 | 受影响任务**重采** + 重判 | 记忆面/时间锚类改动影响面大 |
| 换判官模型/参数 | 新批次 + A/A + 如需则 multirun 多数投票 | 绝不复用旧缓存冒充新观测 |
| 纯报告/指标改动 | 重算 `report_v2` / `radar_v2` / 分析工具 | 不碰 evidence/results |
| 每次任何改动后 | 复算链幂等 + 全套测试 + 更新本卡 sha 表 | 失败批保留、换新编号 |
