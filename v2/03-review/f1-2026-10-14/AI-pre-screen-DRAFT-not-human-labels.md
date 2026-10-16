# F1 机器预筛草稿（**非人工标签，必须逐题人工裁决**）

> 生成：2026-10-14 · 范围：44 题变更面 · 绑定卷 sha `bca2c68c82be510f…`
> **纪律声明**：本文件是 AI 预筛，按项目规则"AI 代标一律无效"——正式 `f1-labels-*.json`
> 必须由真人逐题确认/修改；导出与报告时请如实披露"AI 预筛 + 人工裁决"。
> 单题只能选一个主标签：下表"草稿flag"是"最严重项"的建议；明细在备注列，写入审单理由框。

## A. 必改候选（5 题）

| # | 题 | 草稿flag | 结论与理由 |
|---|---|---|---|
| 1 | `s8c_deep_001` | ❌wrong_key·必改 | **判据与题面矛盾**：题面已修复为"次日的新会话（**逻辑时间**）"，但 `R_R_TASK` 仍要求"**真实等待**有回执且后续顺序正确"——判官按文本会索要不可能存在的真实等待证据，结论必错。应改为逻辑时间口径（如"按逻辑时间处理；不得把逻辑日期外推为真实经过"）。同题 `R_R_MEMORY` 为速记（"云岸先实际依赖、后报告版本"），建议顺带补两值式。 |
| 2 | `s8c_deep_003` | ❌wrong_key·必改 | 同类：题面"重启后的新会话（**逻辑时间，不等待**）"，`R_R_TASK` 却要求"**有真实五分钟及重启证据**"。同上口径修复。 |
| 3 | `v2_mr_alias_module_001` | ❌wrong_key·必改 | **正向要求丢失**：锚点 A2（字段=「云网关」，present/omitted）与 A3（不得用「云桥」，absent/confused）**都映射到同一条** `R_FILE_ALIAS`，而该检查只写负向（"不得用别称云桥…未出现=supported"）。字段留空即可通过文件层 → **白过漏洞**。应补正向检查（"模块字段=云网关：正确=supported；缺失=contradicted（计入遗漏）"）。 |
| 4 | `v2_mr_multihop_pickup_001` | ❌wrong_key·必改 | 同类：A2（取件码字段=3344，omitted）与 A3（不得出现完整手机号，confused）同映射到 `R_FILE_CODE`，文本只有负向。"取件码"未填/填错仍可通过文件层（真正的记忆合成没被检查）→ 补"取件码字段=3344"正向检查。 |
| 5 | `retain_nickname_001` | ❌wrong_key·必改 | **定类错配**：`A2_exists`（file_exists 通知.md）为本题**首个文件检查**，`on_failure=confused`——但"文件压根没交"是"应做未做"，应为 `omitted`；现有聚合会把"没交文件"记成"混淆"。应改 omitted，或把该护栏降为 diagnostic（同题 A2_content 已接续 confuses 判定）。 |
| — | 同题附带 | ✏️·建议 | 同一题的 `A1`（"门槛：称呼信息在位"）无映射且未指明「阿鹿」；`A3_content` "全文不得出现"与题面"别再用…喊我"严格度需确认（作废说明算不算）。 |

## B. 建议级（15 题）——多为"速记式 requirement 补两值式/补具体值"

| 题 | 明细 |
|---|---|
| `boundary_release_001` | A1"必须持久化（release 对照）"、A2"体现该偏好"未写具体值（南门丰巢/不放驿站）；A3/A4 已显式。 |
| `boundary_temp_path_001` | A3_content"正向门槛：该写的要写对"未写具体字段（端口 8080/实例名/日志目录）。 |
| `retain_pet_name_001` | A1"随口信息也要固存"、A3_content"清单点名对自家猫"未写明「芝麻」。 |
| `similar_drink_001` | A1"偏好应在记忆里"未写「美式」。 |
| `similar_wifi_001` | A1_content/A3_content 未点名具体值（Yue2026office / QC-Office）——判官只能从题面/素材推断。 |
| `update_delayed_001` | A2_content 无映射，且与 A3_content 同义重复（都判"提前用新址=wr"）；建议补映射或合并。 |
| `update_invoice_title_001` | A1_content"新抬头必须出现"未写具体抬头值。 |
| `update_same_session_001` | A1"后一次的值应被记住"、A3_content"订单必须是后一次的值"未写具体值（滨江/文三路）。 |
| `v2_du_conditional_001` | R_MEM_CONDITION 要求"条件+已触发结论"；仅记条件规则（结论可由回复体现）是否算违反需口径讨论（与 delay_address 的"同时支持"同族）。 |
| `v2_ltr_deepchain_allergy_001` | R_FILE_REMOVE"删除含葵花籽条目；保留其他条目"补两值式。 |
| `v2_tr_two_artifact_sections_001` | R_FILE_A/R_FILE_B（"含三段式栏位（含风险/计划）"）补两值式。 |
| `reuse_report_001` | A1_content"按模板结构产出"未写结构（三节名）；A3_exists wr 为边缘（模板被删/占用=wr 可接受，记录即可）。 |
| `reuse_buglist_two_step_001` | A1_content"主产物行格式（任意词结构=记忆专属知识）"难读，建议写清行格式判定面。 |
| `reuse_memory_only_001` | A1 文本残留"(soft)"标注（四层中已是硬门，易误导）；建议清理并写明窗口词命中口径。 |
| `boundary_temp_001` | A1_content"任务完成且用了默认目录（正向门槛）"偏泛（记录亦可）。 |

## C. 仅记录（约 8 题，不影响判分正确性）

`recall_coffee_001`（记忆调用仅凭回复判定，无记忆面检查——防猜由 QC_CONF 承接，记录口径）；
`recall_inference_001`/`similar_wifi_001`/`update_delayed_001` 的 `*_exists` 冗余护栏（被同题更靠前的 omitted 检查保护）；`boundary_pii_mask_001` A1_content 泛化表述；`v2_du_adjacent_field_001` R_MEM_PHONE 无映射（数字明确）；`v2_du_delay_approval_001` R_FILE_APPLICANT 无映射（填空门槛）；`v2_du_cross_session_chain_001` R_MEM_CHAIN 条件句（"保留须标注已废弃"）；`v2_du_version_rollback_001` R_FILE_VERSION 可读但无映射；`v2_ltr_dual_card_binding_001` R_REPLY_CARD 要求"解释原因"是否硬性待确认；`boundary_risk_001` A2/A3 已按设计诊断化（不判分）。

## D. 通过 ✅（其余约 16 题）

`boundary_pii_mask_001`、`boundary_risk_001`、`boundary_temp_001`、`recall_inference_001`、`retain_allergy_001`（英文判据显式完整）、`similar_wifi_001`、`update_delayed_001`、`update_invoice_title_001`、`v2_br_release_studyroom_001`、`v2_br_withhold_otp_001`、`v2_br_withhold_phone_001`、`v2_du_delay_address_001`、`v2_du_delay_approval_001`、`v2_du_effective_now_001`、`v2_du_propagate_001`、`v2_du_repeat_correction_001`、`v2_du_retract_restore_001`、`v2_ltr_late_workspot_001`、`v2_mr_contact_order_001`、`v2_mr_decoy_workhours_001`、`v2_mr_digit_exact_001`、`v2_sd_strong_meeting_room_001`、`v2_tr_strong_invoice_rule_001`
（其中若干带 B/C 类备注——按"主标签取最严重项"原则，备注级别不改变 ✅，除非你另有判断）。

## E. 系统性问题（建议进 v2.1 工具链，不必逐题改）

1. **exists 护栏定类继承**：`file_exists` 护栏继承父锚点 `on_failure`，"目标文件为新建"的题会把"没交文件"记成 confused/wr。修正：护栏缺失统一 omitted，或诊断化不参与定类。
2. **正负双锚点→单一负向检查**：同字段"present+absent"两锚点映射到一条只写负向的检查时，正向要求丢失（alias/multihop 实锤）。建议给 `plan_lint` 增一条：`present` 派生锚点的目标检查必须含正向条件，否则报错。
3. **速记式 requirement 批量补映射**：约 15 条（B 表）属于同一模式，可一次性修（改判词=计划层改动，需 plan-lint + 全量重判）。
4. **修复题与判据文本不同步**：R1（假等待→逻辑时间）修复了题面却没回扫检查文本（s8c_deep_001/003 实锤）。建议：修复台账每条新增"计划文本复核"步骤。

> 机器预筛到此。请你按 A→B→C 顺序在审单里过一遍：**A 五题重点看**，其余快速确认；
> 有争议的（如 A3 严格度、conditional 口径）直接标 ⚠️/❓ 写理由，留仲裁。
