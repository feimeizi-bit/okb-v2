# WP1-B 修复清单（14 乙 + 2 丙）

> 全部修复对象：只改判据/锚点/路径/时间声明，**不改历史成绩**；
> 修复后按硬检 1–8 重过 lint + anchorcal 预检，才进 v2 核心。

## 丙级（重写，2 题）

| 题 | 问题 | 修复方案 |
|---|---|---|
| `s8c_deep_001` | 题面称"真实经过24小时"但执行器不等待 | 二选一：①改逻辑时间（faketime + 题面写"跨会话/逻辑时间"）②保留真实等待则必须真等（成本入预算）。默认 ①，标题同步改 |
| `s8c_deep_003` | 称"真实至少五分钟并重启" | 同上；"重启"若无真实重启则删去或改为会话切换 |

## 乙级（16 条缺陷分四类）

### 1) 路径钉死（8 条锚点 / 4 题）
`boundary_temp_001.A1`、`boundary_temp_path_001.A1/A2`、`reuse_report_001.A1/A2/A4`、
`reuse_memory_only_001.A2a/A2b/A2c/A3`
**修法**：改用真实产路径集合（run_v02_first 实测出现过 `报告.md`、`输出-临时/报告.md` 等）；
锚点改 `path_regex` 或改断到文件内容；题面若未明确路径，一律不钉死。

### 2) reply 回声 W212（7 题）
`retain_allergy_001`、`recall_coffee_001`、`update_delayed_001`、`similar_drink_001`、
`boundary_risk_001`、`boundary_pii_mask_001`、`boundary_release_001`
**修法**：删除共享词元的 reply hard 锚点 → 降 soft 或改为产物锚点；
语义判定交给计划检查（v2 标准：reply 只留"非空"结构护栏）。

### 3) trap 未覆盖 W206（7 题）
`update_delayed_001`、`reuse_report_001`、`reuse_memory_only_001`、`update_route_001`、
`update_invoice_title_001`、`reuse_buglist_two_step_001`、`boundary_temp_path_001`
**修法**：v2 模型下 trap 自动成为计划检查（生成器展开），或删除无判据承接的 trap 注释。

### 4) 无区分力锚点 W207（8 条）
`retain_allergy_001`×2、`update_delayed_001`、`reuse_report_001`×4、`boundary_risk_001`、`reuse_buglist`…（详见 wp0-summary）
**修法**：加 `must_differ_from_fixture` 或改语义断言；"旧值保持"型写明豁免理由。

### 5) 其他
- `boundary_pii_mask_001` W208 事件锚点：声明事件覆盖率或整条 skip；
- W202（6 题）：补 `evaluate_at: snapshot_after`；
- W205（39 题注记）：保留，逐题登记优先级说明即可。

## 修复后准入

每题修复后跑：`validate.py`（该题相关 W 清零/豁免登记）→ F1 审题单复核 →
入选 v2 核心或备选池。
