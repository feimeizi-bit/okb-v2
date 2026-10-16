# okb-v2 方案补充：数据生成、覆盖与评分口径（交付 a 的"数据生成方式"节 + 校准证据）

> 2026-10-13。本文补齐赛题交付 a 要求的两节：**数据生成方式** 与 **自动评分流程**，
> 并附真机校准证据（item 难度/区分度、双口径指标、检查族拆分）。
> 事实边界：所有数字绑定 `runs/v2r3_20261010-healed`（552 任务）与
> `score/judge-full-3-replay`（判官 gpt-6.1-sol，1674 次调用）；paper sha `bca2c68c…`，
> plan sha `7643b43a…`。未完成的验证（人审、A/A 判官复跑）在 §7 如实列出。

## 1. 数据结构

- **样本（sample）五件套**：`script`（多会话剧本，相位 `write < disturb < update < probe` 单调）
  + `fixture`（环境初态文件）+ `anchors`（规则锚点）+ `expect`（正确行为/错误对照声明）
  + `plan_checks`（判官语义检查，装配期拆入计划层）。
- **两类判据**：`anchors` 走规则执行（六类证据通道：memory_entry / memory_read / memory_write /
  file_content / reply_text / tool_call）；`plan_checks` 走判官三态
  （supported / contradicted / insufficient）。断言集合 = anchors ∪ plan_checks。
- **六维**：长期保持 / 记忆调用 / 动态更新 / 相近区分 / 边界识别 / 任务复用；
  配额 15/15/15/15/15+17；边界子集 withhold 10 / release 5。
- **五分类**：remembered / omitted / confused / wrongly_persisted（wp）/ wrongly_reused（wr）；
  wp/wr 单列风险指标，不进总分成分子。
- **证据面**：对话（逐轮 raw）、记忆前后快照（含 coverage 扫描范围）、文件读写前后内容、
  工具/时间观测；PII 先判定后替换成占位符落盘。

## 2. 数据生成方式（可复算链）

```
作者稿(.yaml) ──v2_split_plan.py──> .fixed.yaml + .plan.json（计划片段）
存量卷(paper-r1) ──build_stock_v2.py──> stock-v2.yaml + stock-v2.plan.json（选举+20 类修复台账）
四部件 ──assemble_v2.py──> paper-v2.yaml + paper-v2.plan.json
         （判官口径归一化 R12 + plan-lint E201/E202 阻断 + validate_plan）
──build_registry.py──> registry-v2.json（629 槽位=330 检查+299 锚点）
──qc_v2.py──> qc-v2-report.md + expect-cells-v2.json（红线 1/2/3/4/5）
```

- **确定性**：整链同输入同 sha（实测连续两次重跑 46 个产物 **0 changed / 0 added / 0 missing**）；
  paper sha `bca2c68c82be510f`。
- **出题硬检 8 条**（`sample-template.yaml` 头注释）：四形态计划层覆盖；≥1 内容谓词判据；
  路径相对化；reply 无回声；时间真实性二选一；lint 0 error 且 W206/W208/W212=0；
  双读者自查；anchorcal 预检。
- **判官输入纪律（R12，2026-10-13）**：94 条 semantic requirement 统一改写为
  「合规命题 + supported/contradicted 显式映射」；新增 `tools/plan_lint.py`
  （E201 元注释/裸失败类、E202 absent 派生缺映射）并在装配期阻断。
  修复前审计：552 任务中 17 个被极性反判判死（11 应 pass / 6 应 unassessable）、2 个假通过；
  修复后全量重判验收：wifi 检查 6/6 翻转正确、17 个任务全部纠正。
- **trap 台账**：45 条声明的诱错值入 `trap-ledger.json`，失败形态由计划检查承接（W206=0）。
- **lint/校准**：样本层 0 error / 61 warning（W205×52 注记、W207×9 书面豁免）；
  anchorcal 死谓词 0 / 路径从未落盘 0。

## 3. 覆盖面与真机校准

**配额**：六维 15/15/15/15/15+17；withhold 10 / release 5；深化标签（时间/强扰动/多步）见 QC 报告。
**证据覆盖**：552/552 任务证据有效（T2 回执绑定快照，546 个记忆依赖项均为 T2；T0 仍然不判）。

**item 难度/区分度台账**（`v2/06-qc/item-stats-v2.md`，口径见工具 docstring）：

| 维度 | 平均难度 | 平均区分度 | 高区分题(≥0.6) | 饱和题 | 地板题 | 重复波动题 |
|---|---|---|---|---|---|---|
| 长期保持 | 0.737 | 0.526 | 7 | 5 | 0 | 4 |
| 记忆调用 | 0.767 | 0.289 | 4 | 8 | 1 | 3 |
| 动态更新 | 0.511 | 0.381 | 6 | 2 | 3 | 7 |
| 相近区分 | 0.878 | 0.119 | 1 | 9 | 0 | 5 |
| 边界识别 | 0.622 | 0.238 | 2 | 5 | 2 | 4 |
| 任务复用 | 0.399 | 0.346 | 3 | 1 | 3 | 7 |

- 全卷：高区分 23 题、饱和（天花板）30 题、地板 9 题、重复波动 30 题；
  饱和/地板/波动清单供人工复核与下一版加难/换判据。
- 旧 QC 报告里占位但未定义的"T1−T2≥0.6"已由上述**跨 Agent 通过率差 |Δ|** 口径替代。

## 4. 自动评分流程

1. **证据门**（layer1）：任务状态、对话完整性、raw 来源、记忆扫描范围、文件指纹；
   不通过 → `unassessable`（拒判，不进 0 分）。
2. **客观检查**：file_exists / file_unchanged / json_field_equals（严格 JSON + JSON Pointer）。
3. **语义检查**（判官三态）：判官看不到答案标签；必须给出真实 evidence_id + JSON pointer + 原文引用；
   引用校验不过 → insufficient。
4. **聚合**：任一 mandatory 检查 contradicted → fail（取计划序第一失败类）；
   任一 insufficient → 分类未定（`unresolved`）或整条 unassessable；全过 → remembered。
5. **可解释性**：每检查保留 result / rationale / citations；样例包 `v2/07-deliverable-b/`
   给出"任务 + 证据切片 + 逐检查解释 + 复现命令"三件套。

**双口径指标**（`v2/06-qc/metric-bounds-v2.md`）：口径 A（assessed-only）与口径 B
（unassessable 按失败，下界）必须并列披露；检查族拆分（memory-store / artifact / reply /
task-application / other）回答"分差差在记忆面还是行为面"。**族名只是 check-id 启发式分流**：
逐条对剧本核查后，`task-application` 族（R_R_TASK / *_CONF）绝大多数是**跨会话记忆的应用**
（约定/规范/偏好写在早前会话、探测轮要求使用），属构念之内；其中混有少量非记忆的产物
质量/一致性检查（主要在 `A1_content_CONF`），去留待构念审计，不得按族名直接下构念结论。

| Agent | 计划 | 通过/失败/不可判 | 口径A | 口径B | 第一失败族构成 |
|---|---|---|---|---|---|
| hermes | 276 | 194 / 43 / 39 | 0.819 | 0.703 | 记忆面 13 · 产物 6 · 回复 4 · 其他 20 |
| openclaw | 276 | 132 / 116 / 28 | 0.532 | 0.478 | 记忆面 36 · 任务应用 30 · 产物 26 · 回复 6 · 其他 18 |

## 5. 五类判定与证据通道映射

| verdict | 典型触发 | 主要证据通道 |
|---|---|---|
| remembered | 全 mandatory 检查 supported（含正确拒记） | 记忆终态 / 产物内容 / 回复 |
| omitted | 应记未记 / 应做未做 | 记忆快照、产物存在性、回复 |
| confused | 张冠李戴、对象/字段串台、格式错位 | 产物 field_path、回复、记忆条目 |
| wrongly_persisted | 不应持久化却入记忆（含打码形态） | 记忆终态、事件流（如可采） |
| wrongly_reused | 已失效/未生效值被当当前值使用 | 产物字段、回复、时间窗口 |

## 6. 稳定与复现

- 采集/构建/评分链全部确定性；`input-lock.json` + `complete.json` 锁输入与产物哈希；
  `register_run.py` 预登记；失败批保留、不覆盖、零重试。
- 判官批次稳定性（A/A，2026-10-13）：同计划同请求两轮，请求级翻转 21/1674 = **1.25%**
  （决定性 supported↔contradicted 仅 2 条 = 0.12%；其余为 sufficient↔insufficient 弃权摆动），
  任务级变化 12/552 = 2.17% → 低于 3% 阈值，单批结论可引用；报告见 `v2/06-qc/judge-aa-v2.md`。
- 本机回归：`engine/tests` 42 passed；`tools/tests` 872 passed / 63 skipped / 0 failed
  （skip 均带理由：VM 同步范围、权限、旧仓冻结链产物）。

## 7. 已知限制（如实披露）

1. 真人标签 = 0：绝对分未经人机一致性（κ）校准，正式对外只应给相对趋势。
2. 记忆面可比性未证：T2 只是回执绑定，非带外验证；9（openclaw）/6（hermes）个任务
   出现"回复全对但记忆面挂"，需探针实验判定是否存在采集面偏差。
3. 覆盖率门槛：hermes 动态更新 0.73、任务复用 0.65 低于规格 80% 门槛，只进雷达不进"正式总分"。
4. 边界/PII 样本薄：has_pii 仅 2 题；时间深化中仅 1 题使用引擎时间锚（其余靠夹具日期文本）。
5. 判词敏感性：R12 证明单题结论对 requirement 写法高度敏感，后续改判词必须走 plan-lint。
6. 失败归因粒度：记忆应用只能在回复/产物面上观测，"记了但没按约定用"与"没记住"在五分类里
   不可分（都落 confused/omitted）；`task-application` 族中少量非记忆的产物质量/一致性检查
   （`A1_content_CONF`）尚未逐条审计去留——这是披露项，不是已修复项。

## 8. 创新点（评审用）

1. **证据分级 T0–T3 + 四层评分**：证据可信门/客观检查/语义三态/校准队列分离，
   "语义支持不能抹掉客观失败"。
2. **判据效力工程**：requirement 极性 lint（E201/E202）、anchorcal 死谓词校准、
   W207 区分力注记、trap 台账与展开幂等。
3. **反刷分护栏**：E119 无有效正向门槛拦截、absent 空集/历史叙述裁决、
   "采不到≠没记住"的 skip 家族、判官不可见答案标签。
4. **可审计判官**：三态协议 + 引用校验（真实 ID/pointer/quote）+ 精确回放 +
   预算账本/调用日志；全量重判可验收（本轮 1674 调用 0 失败）。
5. **确定性数据生产线**：作者稿→卷→计划→断言库→质检全链同 sha 复算，
   修复台账与归一化台账逐条可溯源。
