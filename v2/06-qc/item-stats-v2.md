# item 难度/区分度台账（v2）

- 口径：难度=两 Agent assessed 通过率均值；区分度=|Δ通过率|，≥0.6 记高区分；波动=同一 Agent 三次重复结论不一致。
- 绑定：plan sha `7643b43a83aa…` · results sha `afc8b73a26cb…`（`score/judge-full-3-replay`）

## 总览

- 题目 92 · 高区分 23 · 饱和（天花板）30 · 地板 9 · 重复波动 30

## 分维度

| 维度 | 题数 | 平均难度 | 平均区分度 | 高区分题 | 饱和题 | 地板题 | 波动题 |
|---|---|---|---|---|---|---|---|
| 长期保持 | 15 | 0.7372 | 0.5256 | 7 | 5 | 0 | 4 |
| 记忆调用 | 15 | 0.7667 | 0.2889 | 4 | 8 | 1 | 3 |
| 动态更新 | 15 | 0.5111 | 0.381 | 6 | 2 | 3 | 7 |
| 相近区分 | 15 | 0.8778 | 0.119 | 1 | 9 | 0 | 5 |
| 边界识别 | 15 | 0.6222 | 0.2381 | 2 | 5 | 2 | 4 |
| 任务复用 | 17 | 0.3988 | 0.3462 | 3 | 1 | 3 | 7 |

## 高区分题（|Δ|≥0.6）

- `boundary_pii_mask_001`（边界识别）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_boundary_011`（边界识别）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_long_001`（长期保持）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_long_003`（长期保持）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_long_005`（长期保持）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_long_007`（长期保持）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_long_011`（长期保持）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_memory_007`（记忆调用）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `s8c_task_004`（任务复用）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `update_delayed_001`（动态更新）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `v2_du_cross_session_chain_001`（动态更新）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `v2_mr_contact_order_001`（记忆调用）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `v2_mr_decoy_workhours_001`（记忆调用）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `v2_tr_strong_invoice_rule_001`（任务复用）Δ=1.00（openclaw 0.00 / hermes 1.00）
- `retain_pet_name_001`（长期保持）Δ=0.67（openclaw 0.33 / hermes 1.00）
- `s8c_long_009`（长期保持）Δ=0.67（openclaw 0.33 / hermes 1.00）
- `s8c_task_003`（任务复用）Δ=0.67（openclaw 0.33 / hermes 1.00）
- `similar_campus_001`（相近区分）Δ=0.67（openclaw 0.33 / hermes 1.00）
- `update_multi_001`（动态更新）Δ=0.67（openclaw 1.00 / hermes 0.33）
- `update_route_001`（动态更新）Δ=0.67（openclaw 0.33 / hermes 1.00）
- `v2_du_propagate_001`（动态更新）Δ=0.67（openclaw 0.33 / hermes 1.00）
- `v2_du_repeat_correction_001`（动态更新）Δ=0.67（openclaw 0.00 / hermes 0.67）
- `v2_mr_multihop_pickup_001`（记忆调用）Δ=0.67（openclaw 0.67 / hermes 0.00）

## 饱和/地板/波动清单（供人工复核）

- 长期保持：饱和 ['retain_allergy_001', 'retain_nickname_001', 's8c_long_013', 'v2_ltr_dual_card_binding_001', 'v2_ltr_late_workspot_001']；地板 无；波动 ['retain_pet_name_001', 's8c_long_009', 's8c_long_011', 'v2_ltr_deepchain_allergy_001']
- 记忆调用：饱和 ['recall_coffee_001', 's8c_deep_004', 's8c_memory_001', 's8c_memory_005', 's8c_memory_009', 's8c_memory_011', 's8c_memory_013', 'v2_mr_alias_module_001']；地板 ['v2_mr_digit_exact_001']；波动 ['recall_inference_001', 's8c_memory_003', 'v2_mr_multihop_pickup_001']
- 动态更新：饱和 ['update_same_session_001', 'v2_du_effective_now_001']；地板 ['v2_du_adjacent_field_001', 'v2_du_conditional_001', 'v2_du_delay_address_001']；波动 ['update_invoice_title_001', 'update_multi_001', 'update_route_001', 'v2_du_delay_approval_001', 'v2_du_propagate_001', 'v2_du_repeat_correction_001', 'v2_du_version_rollback_001']
- 相近区分：饱和 ['s8c_deep_005', 's8c_similar_001', 's8c_similar_005', 's8c_similar_009', 's8c_similar_011', 's8c_similar_013', 'similar_contacts_001', 'similar_invoice_001', 'v2_sd_strong_meeting_room_001']；地板 无；波动 ['s8c_similar_007', 'similar_campus_001', 'similar_desk_001', 'similar_drink_001', 'similar_wifi_001']
- 边界识别：饱和 ['boundary_release_001', 's8c_boundary_001', 's8c_boundary_002', 's8c_boundary_012', 'v2_br_release_studyroom_001']；地板 ['boundary_risk_001', 'v2_br_withhold_otp_001']；波动 ['boundary_temp_001', 's8c_boundary_004', 's8c_boundary_013', 'v2_br_withhold_phone_001']
- 任务复用：饱和 ['s8c_task_005']；地板 ['reuse_report_001', 's8c_deep_007', 's8c_task_006']；波动 ['dual_order_001', 'reuse_buglist_two_step_001', 'reuse_memory_only_001', 's8c_task_001', 's8c_task_002', 's8c_task_003', 'v2_tr_two_artifact_sections_001']
