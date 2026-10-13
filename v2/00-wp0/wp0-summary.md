# WP0 旧卷质检分级汇总

| 维度 | 甲 | 乙 | 丙 | 合计 | 目标核心15缺口 |
|---|---|---|---|---|---|
| long_term_retention | 17 | 1 | 2 | 20 | 0（富余 3） |
| memory_recall | 17 | 1 | 0 | 18 | 0（富余 3） |
| dynamic_update | 2 | 3 | 0 | 5 | +10 |
| similar_discrimination | 21 | 1 | 0 | 22 | 0（富余 7） |
| boundary_recognition | 15 | 5 | 0 | 20 | 0（富余 5） |
| task_reuse | 12 | 3 | 0 | 15 | 0（富余 0） |
| **合计** | 84 | 14 | 2 | 100 | — |

## 缺陷登记（关键词表）

### P0（丙级：假真实等待 / 无计划检查 / 全卷无内容判据）
- `s8c_deep_001` (long_term_retention): P0:fake_real_wait
- `s8c_deep_003` (long_term_retention): P0:fake_real_wait

### P1 路径钉死（anchorcal path_never_produced）
- `boundary_temp_001`: 1 条 — P1:path_pinned(A1);note:W205x1
- `reuse_report_001`: 3 条 — P1:path_pinned(A1,A2,A4);P1:trap_uncovered(W206x1);P1:weak_anchor(W207x4);note:W205x1
- `reuse_memory_only_001`: 4 条 — P1:path_pinned(A2a,A2b,A2c,A3);P1:trap_uncovered(W206x1)

### W206 trap 未被锚点覆盖（7 题）
- `update_delayed_001`x1, `reuse_report_001`x1, `reuse_memory_only_001`x1, `update_route_001`x1, `update_invoice_title_001`x1, `boundary_temp_path_001`x1, `reuse_buglist_two_step_001`x1

### W207 锚点无区分力（4 题）
- `retain_allergy_001`x2, `update_delayed_001`x1, `boundary_risk_001`x1, `reuse_report_001`x4

### W208 事件锚点不可校准（1 题）
- `boundary_pii_mask_001`x1

### W212 reply 与参考答复共享词元（回声）（7 题）
- `retain_allergy_001`x1, `recall_coffee_001`x1, `update_delayed_001`x1, `similar_drink_001`x1, `boundary_risk_001`x1, `boundary_pii_mask_001`x1, `boundary_release_001`x1

## 边界口径计数（withhold/release 目标 ≥10/≥5）：{"withhold": 14, "release": 6}

## 深化标签现有项（tags/time_anchor）
- `update_delayed_001` (dynamic_update): tags=- time_anchor=True
- `update_invoice_title_001` (dynamic_update): tags=time_anchor time_anchor=False
- `similar_wifi_001` (similar_discrimination): tags=strong_disturb time_anchor=False
- `reuse_buglist_two_step_001` (task_reuse): tags=multi_step time_anchor=False