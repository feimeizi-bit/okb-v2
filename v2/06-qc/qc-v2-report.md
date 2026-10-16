# paper-v2 质检报告（对照 B 曾被打回的质检环节）

## 红线 1 · 每维混淆判据（三读法，验收=C）

| 维度 | 题数 | A 锚点字面 | B 计划confused | C 内容错覆盖 | C≥8 |
|---|---|---|---|---|---|
| long_term_retention | 15 | 4 | 16 | 18 | ✓ |
| memory_recall | 15 | 5 | 15 | 16 | ✓ |
| dynamic_update | 15 | 7 | 23 | 42 | ✓ |
| similar_discrimination | 15 | 18 | 25 | 25 | ✓ |
| boundary_recognition | 15 | 1 | 2 | 20 | ✓ |
| task_reuse | 17 | 4 | 17 | 21 | ✓ |

**C 读法全维达标：True**（A/B 同时公示，不做选择性引用）

## 红线 2 · 正反对称（v1.1 已撤销硬门，仅披露）

- 边界口径分布：{'withhold': 10, 'release': 5}（目标 withhold≥10 / release≥5）
- verdict 分布：{'remembered': 92}

## 红线 3 · 五档预期格（生成器产物）

- `expect-cells-v2.json` 已生成：92 格（每格含 verdict/失败面/陷阱/boundary/深化标签）
- gamed 失败面：**每格均有失败检查面**；缺内容错判据的样本：['s8c_boundary_011 — by-design（release：失败面=过度拒记 → omitted）', 's8c_boundary_012 — by-design（release：失败面=过度拒记 → omitted）', 's8c_boundary_013 — by-design（release：失败面=过度拒记 → omitted）', 'v2_ltr_late_workspot_001 — by-design（无错误值面，仅 omitted）', 'v2_br_release_studyroom_001 — by-design（release：失败面=过度拒记 → omitted）']
- 全量区分度台账：见 `06-qc/item-stats-v2.md`（工具 `tools/item_stats.py`；口径=跨 Agent 通过率差 |Δ|，≥0.6 记高区分；真机数据，绑定 judge-full-3-replay）

## 红线 4 · anchorcal（对批次三全局语料）

- 锚点 299：可判 166，不可判 129
- **死谓词 0** · **路径从未落盘 0** · 未证实负例 2 · 恒真(结构护栏) 129
- 覆盖：samples_covered_by_run=12/92（新题尚无证据，冒烟后复跑）
- 未证实负例明细：
  - `update_multi_001` A6: 正则 '13900000000' 在 707 条真实语料上零命中：这条 absent 判据从不触发，分不清『正确克制』与『死判据』
  - `update_multi_001` A7: 正则 '13900000000' 在 707 条真实语料上零命中：这条 absent 判据从不触发，分不清『正确克制』与『死判据』

## F1 人工审题 · F2 配额

- F1：审题单**未生成**（下一步：只审变更面 = 24 新题 + 20 修复题，以修复台账为准）
- F2：六维 15/15/15/15/15/17 ✓；withhold 10 / release 5 ✓；深化标签 {'cross_session': 92, 'multi_turn': 92, 'has_gold_evidence': 92, 'has_fixture': 44, 'has_pii': 2, 'has_time_anchor': 1, 'has_tool_assertion': 1}

## 历史失败模式逐条对照（B 被打回的坑）

| B 的坑 | v2 实测 |
|---|---|
| 路径钉死 8 条 | anchorcal path_never_produced = 0 |
| 假真实等待 2 题 | 扫描命中 = 0 |
| reply 回声 7 题 | lint W212 = 0 |
| trap 无承接 7 题 | lint W206 = 0（45 trap 入台账） |
| 事件锚点不可校准 1 条 | lint W208 = 0 |
| 五档格缺失 | expect-cells-v2.json 已生成 |
| 配额缺口（DU 5/15 等） | 六维各 15 ✓ |
| 卡源单点（cards-r3） | 24 新题在仓（holdings 作者稿） |
| pilot 门不可达（结构性死门） | 走自有链，无此门 |
| 确定性 source_invalid ×4 | **风险仍在**：['update_multi_001', 'boundary_pii_mask_001'] 需冒烟确证/修表示 |

## 红线 5 · 计划判官口径 lint（极性防线，2026-10-13 新增）

- 语义检查 requirement：E201（元注释/裸失败类）0 · E202（absent 派生缺 supported/contradicted 映射）0；error 合计 0
- 判官口径归一化台账 `plan-requirement-normalization.json`：94 条改写（R12；含混淆判据模板自动改写）
- 背景：552 任务判官审计确认 17 个任务被极性反判判死（11 应 pass / 6 应 unassessable）、2 个假通过；本 lint 为阻断级防线

## 工程门（已过）

- build 0 error；`validate_plan` 通过（330 检查）；lint {'205': 52, '207': 9}（W205/W207 注记+豁免）；plan-lint 0 error