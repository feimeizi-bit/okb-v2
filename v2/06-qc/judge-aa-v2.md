# 判官 A/A 稳定性（同请求多轮）

- 请求 1674 · 翻转 21（1.25%）· 轮数 2

## 结果迁移（A→B）

- `supported->insufficient`：7
- `contradicted->insufficient`：6
- `insufficient->supported`：3
- `insufficient->contradicted`：3
- `supported->contradicted`：2

## 翻转最多的检查（Top 15）

- `R_MEM_PHONE`：3
- `A2_content`：2
- `R_R_MEMORY`：2
- `pilot_reuse_report_001_A6`：2
- `QC_CONF_ROBUST`：2
- `pilot_boundary_risk_001_A5`：2
- `pilot_boundary_temp_path_001_A1`：1
- `pilot_boundary_pii_mask_001_A5`：1
- `R_MEM_OTP`：1
- `pilot_update_multi_001_A2`：1
- `R_MEM_VERSION`：1
- `R_REPLY_TEMPLATE`：1
- `A1_content`：1
- `R_R_TASK_CONF`：1

## 任务级

- 任务结论变化 12 / 552（2.17%）；示例：hermes-boundary_temp_path_001-r2, hermes-s8c_deep_007-r3, hermes-s8c_memory_005-r3, hermes-s8c_similar_011-r2, hermes-s8c_task_004-r2, hermes-v2_br_withhold_otp_001-r3, hermes-v2_du_adjacent_field_001-r2, openclaw-boundary_pii_mask_001-r2
