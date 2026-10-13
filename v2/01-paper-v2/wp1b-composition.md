# WP1-B 卷组成与排产（质量优先口径）

> 原则：每维 15 核心中，s8c 卡派生系列**每维保留 7 题**（挑场景差异最大的），
> 优先保留手工题与可修复题；不足用**新著**补足。备选池由压缩出来的系列题组成。
> 全部产出独立版本 v2-draft，旧卷只读。

## 组成账（核心 90）

| 维度 | 系列保留 | 手工/其他保留 | 新著 | 核心合计 | 备选池 |
|---|---|---|---|---|---|
| 长期保持 | 7（s8c_long 择优） | 5（retain_nickname、retain_pet_name、retain_allergy*、s8c_deep_001*、s8c_deep_003*） | **3** | 15 | 8 |
| 记忆调用 | 7（s8c_memory 择优） | 3（recall_inference、recall_coffee*、s8c_deep_004） | **5** | 15 | 8 |
| 动态更新 | 0（本身就 5 题） | 5（multi、same_session、route*、delayed*、invoice_title*） | **10** | 15 | 0 |
| 相近区分 | 7（s8c_similar 择优） | 7（campus/contacts/invoice/wifi/desk/drink*/s8c_deep_005） | **1** | 15 | 8 |
| 边界识别 | 7（s8c_boundary 择优，须满足 withhold≥10/release≥5） | 5（risk*、pii_mask*、temp*、release*、temp_path*） | **3** | 15 | 8 |
| 任务复用 | 0（7 题 s8c_task + 4 题 s8c_deep 本身多样） | 6（dual_order、reuse_report*、reuse_buglist*、reuse_memory_only*、+新著 2 备用余量） | **2** | 17（含 2 余量） | 0 |

`*` = 乙级修复或丙级重写对象（见 wp1b-repairs.md）

## 新著 24 题（含深化槽）

- **DU 核心 10**（其中 2 题时间深化）：多字段更新 / 延迟生效 / 同会话再覆盖 / 撤回-恢复 /
  条件更新 / 单位换算 / 版本回退 / 跨会话更新 / 相邻字段不串台 / 相对时间更新
- **强扰动 3**（写探间 ≥3 话题会话）：LTR 1 题、SD 1 题、TR 1 题
- **其余维度补位 11**：LTR +3、MR +5、SD +1、TR +2（含余量）
- **多步复用**：从存量多步可达题中标注 4 题（不新著）

## 深化槽核对（目标 时间3 / 强扰动3 / 多步4）

| 槽 | 存量 | 新著补 | 合计 |
|---|---|---|---|
| 时间 | 1（delay） | +2（DU 内） | 3 |
| 强扰动 | 0 | +3 | 3 |
| 多步 | 8 | 0（标 4） | ≥4 |

## 出题硬检（每题必过）

1. 四类失败形态（omitted/confused/wp/wr）计划层全覆盖；
2. ≥1 条内容谓词判据（禁纯结构锚点唯一化）；
3. 路径只用相对路径、文件判据锚 `field_path`；
4. reply 判据不与参考答复共享独有词元（W212 清零）；
5. 时间真实性二选一（真实等待或逻辑时间 faketime，题面如实）；
6. lint 0 error；W206/W208/W212 清零；W207 允许但必须写明豁免理由（"旧值保持"型天然与 fixture 一致，区分力由计划检查承担）；W202 逐条写明 `evaluate_at`；
7. 双读者歧义自查（F1 前哨）；
8. anchorcal 预检通过（自产小批证据）。
