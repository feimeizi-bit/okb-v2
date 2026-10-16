# okb · openKylin 智能体长期记忆自动化评测方案（交付 a · v2.0-R12）

> 状态：赛题线冻结版。数字口径与限制见 `docs/data-card-v2.md`；本文件按赛题
> "方案说明文档"要求组织：测试目标、基本前提、**数据生成方式**、验证用例设计、
> 结果收集、自动评分完整流程，并附数据集结构与可扩展性说明。

## 1. 测试目标与痛点对策

**目标**：在 openKylin 上，对通用智能体的长期记忆能力做**自动化、低人工、可复现、可扩展**的
量化评测，产出一套多维指标（六维 + 风险指标 + 覆盖率），用于跨智能体比较与持续优化。

对赛题三个痛点的直接对策：

| 赛题痛点 | 本方案对策 |
|---|---|
| 依赖人工检查、难规模化 | 全流程自动化：引擎清场/发题/取证 → 双轨评分（规则+判官）→ 报告/雷达；人工只在"校准与争议"环节介入 |
| 简单问答验证覆盖不了冲突更新、相似干扰、行动复用 | 92 道多会话情景题：动态更新 15、相近区分 15、任务复用 17（含双产物复用）；跨会话/时间窗口/强扰动结构化设计 |
| 自动评分语义偏移、难稳定判断"记住/误记/不该记却记了" | 五分类判据（remembered / omitted / confused / wrongly_persisted / wrongly_reused）+ 判官三态 + 引用校验 + 答案隔离 + 判词极性 lint + A/A 稳定性验证 |

## 2. 基本前提

- **被测对象**：智能体 CLI（首个批次：OpenClaw 2026.9.5、Hermes v0.21.3），在 openKylin VM 内运行；
- **隔离**：task 级清场（对话上下文、长期记忆、工作区三者都重置），会话键 `<run_id>/<task_id>/<session>` 防串台；
- **公平性**：被测 Agent 只收到用户话术 + 时间前缀 + 初始文件；看不到判据/答案/mock 策略；
- **取证前提**：每任务必产前置/后置记忆快照（带扫描范围自证）与文件前后内容；
- **人为介入下限**：判官在正常链路中无人工参与；人工只用于（a）争议与未决复核、（b）验收期一致性校准（当前为 0，已披露）。

## 3. 数据集设计（可扩展的数据结构）

### 3.1 规模与配额（v2.0）

- **92 题** = 六维核心 90（每维 15）+ 任务复用深化 2；边界维 withhold 10 / release 5；
- 深化槽：时间 4 题（标签）、强扰动 4 题、多步复用 2 题（双产物形态 10 题）。

### 3.2 样本结构（五件套）

```yaml
id / dimension / title
script:      # 多会话剧本；phase 单调 write < disturb < update < probe
  - session: s1, phase: write, time: 2026-05-28T09:00+08:00
    turns: [{text: 用户话术}]
fixture:     # 初始文件（相对路径 + 内容）
anchors:     # 规则锚点：type(六类证据通道) × expect(present/absent) × severity(hard/soft) × on_violation(五分类)
expect:      # verdict(正确类) / reference_answer / gold_evidence / confusions / trap
plan_checks: # 判官语义检查（作者稿内联，装配期拆入计划层）
```

### 3.3 "记忆情形 × 证据面"覆盖矩阵（赛题点名逐项）

| 情形 | 代表题 | 数量 | 评级 |
|---|---|---|---|
| 稳定偏好 | retain_allergy / retain_nickname / similar_drink / recall_coffee… | ≥10 | ✅ |
| 路径/路线 | update_route / update_delayed / v2_du_delay_address / similar_campus | 5 | ✅ |
| 模板/格式 | reuse_report / v2_du_version_rollback / v2_tr_two_artifact_sections | ≥6 | ✅ |
| 动态更新/冲突 | 延迟生效 3 / 回退 / 条件 / 跨会话链 / 相邻字段 / 重复更正 / 双产物传播 | 15 | ✅ |
| 相近区分 | WiFi / 联系人 / 校区 / 会议 / 发票 / 工位 / 饮品 | 15 | ✅ |
| 临时信息 | boundary_temp / boundary_temp_path / s8c_boundary_004/011/013 | ≥4 | ✅ |
| 敏感信息 | boundary_pii_mask / v2_br_withhold_phone / v2_br_withhold_otp | 4 | ⚠ 偏薄 |
| 风险指令 | boundary_risk / s8c_boundary_012 | 3 | ✅ |
| 噪声/干扰 | 全卷 disturb；强扰动 4；干扰值题 decoy / 多跳题 | — | ✅ |

证据面：对话/记忆/文件全卷覆盖；工具面 1 题、引擎时间锚 1 题（v2.1 扩充）。

### 3.4 扩展方式（可扩展性）

新增题 = 写作者稿（模板 `v2/01-paper-v2/sample-template.yaml`，头注释含 8 条出题硬检）
→ `v2_split_plan.py` 拆计划 → `assemble_v2.py` 装配（自动归一化 + plan-lint + validate_plan）
→ `build_registry.py` 入断言库 → `qc_v2.py` 质检。全程确定性、同输入同 sha。

## 4. 数据生成方式（确定性生产线）

```
作者稿(.yaml) ─v2_split_plan─> .fixed.yaml + .plan.json（计划片段）
存量卷(paper-r1) ─build_stock_v2─> stock-v2.yaml + 计划片段 + 修复台账（20 项）
四部件 ─assemble_v2─> paper-v2.yaml + paper-v2.plan.json
        （判词归一化 R12 + plan-lint E201/E202 阻断 + validate_plan）
─build_registry─> registry-v2.json（629 槽位 = 330 检查 + 299 锚点）
─qc_v2─> 质检报告 + 五档预期格
```

- **确定性**：连续重跑 54 个产物 0 漂移；paper sha `bca2c68c82be510f…`；openKylin VM 重建同 sha（见 c 回执）；
- **判词规格**：semantic requirement 必须是"合规命题 + supported/contradicted 显式映射"；
  新增 `tools/plan_lint.py`（E201 元注释/裸失败类、E202 absent 派生缺映射）为阻断级防线
  （背景：曾发生 17 题被极性反判判死、2 题假通过，已修复并全量重判验收）；
- **台账**：`plan-requirement-normalization.json`（94 条改写逐条可审计）、`stock-v2-repair-log.json`（20 项修复）、`trap-ledger.json`（45 条陷阱）；
- **质检四关**：schema/lint（0 error；W205×52 注记、W207×9 豁免）→ loader 语义 → 计划可追溯（validate_plan）
  → 死谓词校准（anchorcal：死谓词 0、路径从未落盘 0）。

## 5. 验证用例设计

- **双层判据**：`anchors` 规则层（可机器复算的正则/存在性/字段断言）+ `plan_checks` 语义层
  （判官按合规命题三态判定）。断言集合 = anchors ∪ plan_checks。
- **防刷分**：E119（无有效正向门槛拦截）、W207（区分力注记）、trap 台账、
  absent 空集语义（0 条目不算"正确克制"）、"采不到≠没记住"的 skip 族、答案隔离（判官不可见 expect/verdict）。
- **边界与安全**：withhold 10 / release 5；敏感值占位符链路（先判定后替换：Agent 收真值、证据落占位符、
  评分在占位符空间，保证回放与隐私）。
- **时间真实性**：不改进程时钟；会话首轮注入剧本时间 + 证据侧时间归一化（mtime/记忆时间戳），
  归一化动作落证据。
- **人工环节（已开跑）**：检查项级 48 例分层盲审已回收——AI 预筛 + 人工裁决，
  人机一致 44/48 = 91.7%（κ 0.76）、**0 假通过**、2 误杀、2 弃权；双人 κ 与 F1 审题按计划（见 §11）。

## 6. 结果收集（证据链）

每个 task 输出三层证据（`runs/<run_id>/`）：

| 层 | 内容 |
|---|---|
| 索引 `evidence.jsonl` | 逐行事件（id/seq/type/actor/source/payload/raw_ref/pii_hits），单一写出口 |
| 真身 `raw/<task>/` | 每轮请求/stdout/stderr、记忆前后快照条目、系统动作、后置文件清单+指纹 |
| 档案 `manifest.json / tasks.jsonl / usage.json` | Agent 身份与配置指纹、552 任务台账、token 用量 |

- **五态**：ok / timeout / agent_error / env_error / skip；**只有 ok 交给评分**，其余不进能力分母；
- **失败也留档**：崩溃/超时同样落 task_summary 与原始输出；批次不覆盖、零重试、换新编号；
- 本批实测：552 任务、证据有效 552/552（记忆依赖项 T2 绑定 546 项）。

## 7. 自动评分完整流程（少人工、可解释）

```
评分器读 evidence+raw
 └─ Layer1 证据门（状态/对话完整性/raw 来源/扫描范围/文件指纹）→ 不过=不可判
 └─ Layer2 客观检查（file_exists / file_unchanged / json_field_equals，严格 JSON）
 └─ Layer3 语义检查（判官三态：supported / contradicted / insufficient；
                      必须给真实 evidence_id + JSON Pointer + 原文引用，引用校验不过=弃权）
 └─ Layer4 聚合与复核队列（任一 mandatory 失败=任务失败，取计划序第一失败类；
                            任一未决=分类未定；全过=正确类）
```

- **判官纪律**：看不到答案标签；模型 gpt-6.1-sol；请求/响应精确回放；调用走预算账本；
- **稳定性证据**：同计划同请求 A/A 两轮，请求级翻转 21/1674=1.25%（决定性翻转仅 0.12%），
  任务级 2.17% → 单批结论可引用；
- **可解释性**：逐检查保存 result / rationale / citations（样例见交付 b）。
- **人机校准**：48 例分层单裁（AI 预筛 + 人工裁决）人机一致 **44/48 = 91.7%**（κ 0.76），
  **0 假通过**；2 误杀均为判据/语义过严（已登记 v2.1 修订），2 弃权均为覆盖声明不足（保守方向）。

### 7.1 核心要素对照（少人工 / 运行证据 / 可解释 / 人审=验证）

| 核心要素 | 实现机制 | 可核查证据 |
|---|---|---|
| **少人工介入** | 规则层纯机器复算；判官协议带机器防线：必须真实 evidence_id + JSON Pointer + 原文引用（校验不过=弃权）、答案隔离、判词极性 lint（E201/E202 阻断级）；缺能力/采不到走 skip 不进分母 | 552 任务零人工跑批；`tools/plan_lint.py`；判官全量 1674 检查 |
| **按运行证据评分** | 证据链三层（事件索引 + raw 真身 + 档案台账；记忆快照带扫描范围自证）；四层评分：证据门 → 客观检查 → 语义判官 → 聚合复核队列 | `runs/v2r3_20261010-healed/`；`score/judge-full-3-replay/` |
| **可解释评分原因** | 检查级 result + rationale + citations（E# + JSON Pointer + 引文原句）；任务级失败类 + 触发检查；报告级维度通过率 + 雷达 + 样例包 | 交付 b（`v2/07-deliverable-b/`）；`score/final-report-r12/` |
| **人审=验证角色** | 判官答案私有、盲审先锁标签后揭示、工具零代填；A（任务级五分类）/B（检查项三态盲审）/C（F1 审题）/D（仲裁）四回路只用于校准判官与发现坏题，不回写分数 | `v2/03-review/` 各审题包；B 环 48 例单裁已回收：人机一致 91.7%、**0 假通过**（数据卡 §3） |

任何维度数字都可沿"报告 → 任务 verdict → 触发检查 → 引文原句"下钻回运行证据；
在人审 κ/一致率达标前，绝对分一律披露为探索性诊断。

## 8. 多智能体与通用性

- **适配器契约**（`engine/adapter.py`）：引擎只发通用动作（capabilities/reset/send/probe_memory…），
  适配器把它翻译成各 Agent 的 CLI 与文件布局，并把输出/记忆统一成同一证据形状；
- **已支持**：OpenClaw、Hermes（真实代理）+ mock（合成考生）+ `cli_template`（声明式接入第三个及以后智能体，
  命令模板 + 字段抽取路径均走 `deploy/agents.yaml`）；
- **缺能力诚实降级**：Agent 不支持记忆导出 → 相关题自动 skip（不假跑、不判负）；
- **落地形态**：`okb` 一键 CLI（薄分发器）+ `.deb`（openKylin 实测安装可用，见 c/d 回执）。

## 9. 指标与报告

- **六维通过率** + 覆盖率；**双口径**：可判口径与"不可判按失败"下界（并列披露）；
- **风险指标**：wrongly_persisted / wrongly_reused 单列，不进总分；
- **覆盖率门槛**（规格自定）：可测样本 ≥10 且样本覆盖率 ≥80%，否则该维只进雷达；
- **校准证据**：item 难度/区分度台账（高区分 23 / 饱和 30 / 地板 9）、检查族拆分
  （记忆面/产物/回复/任务应用）、判官 A/A；
- 样本报告：`score/final-report-r12/`（report.json + index.html + 六维雷达 SVG/JSON）。

## 10. 复现与交付

| 交付 | 位置 |
|---|---|
| a 本文件 | `deliverables/a-方案说明文档.md` |
| b 样例包（任务+证据+评分） | `v2/07-deliverable-b/`（3 案例 + 证据切片 + 复现命令） |
| c 复现材料 | `deliverables/c-复现说明.md` + openKylin 回执（`deliverables/c-openkylin-verify/`） |
| d 工具封装 | `deliverables/d-package/`（.deb + CLI 说明） |
| e 演示视频 | `deliverables/e-录屏脚本.md`（分镜与演示清单，供录制） |

## 11. 已知限制与路线图（如实披露）

1. **人审仅单裁 48 例**：AI 预筛 + 人工裁决（人机一致 91.7%、κ 0.76、0 假通过、2 误杀 2 弃权）；
   无第二人 κ、存在锚定效应，全部绝对分仍按"探索性诊断"披露；
2. 覆盖率不足维度（hermes 动态更新 0.73、任务复用 0.59；openclaw 任务复用 0.65）只进雷达；
3. 记忆面可比性未证（T2 是回执绑定非带外验证）；存在"回复/产物正确但记忆面检查挂"的任务，
   待探针实验；
4. 时间纵深与敏感样本偏薄；工具面证据 1 题；
5. **剧本深度浅**：283 个会话中 272 个为单轮（96%），题内总轮数中位 3（最大 6）；本套测的是
   跨会话持久化与更新冲突（引擎逐会话清场，不靠轮数），**不构成长对话上下文的压力测试**，
   深轮次槽列入 v2.1；
6. 跨 OS 换行差异已修复（`* -text` + 全仓 LF，计划 CRLF 例外按记录绑定保留）；
   v2.1 将统一生成器换行并重新冻结。
7. **v2.1 计划**：时间跨度题 2 + 敏感题 2–3 + 深轮次槽 2–3（单会话 12–20 轮、探测 ≥12 轮后）
   + 深会话链 1–2（6–8 会话、延迟探测） + 干扰对 1–2 + 构念审计 + 记忆面探针 + 人审 ≥50 例（κ）。
