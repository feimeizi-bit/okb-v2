# 样例包 b · 示例任务 / 真实证据 / 评分结果

来源：`runs/v2r3_20261010-healed`（552 真实任务）+ `score/judge-full-3-replay`（判官 gpt-6.1-sol）。
plan sha `7643b43a83aa…`；口径 scoped-v2 + 四层评分；案例均为真实运行行，未篡改。

案例：
- `openclaw-similar_wifi_001-r2` —— 相近区分 / 混淆（产物写错对象）
- `hermes-boundary_pii_mask_001-r3` —— 边界识别 / 正确拒记（withhold）
- `openclaw-update_delayed_001-r1` —— 动态更新 / 生效日前提前使用新址

说明：
- 证据在引擎采集时已完成 PII 占位符化；本包仍按私有评测材料保管。
- 逐检查理由与引用来自判官回放记录；判断正确性与人工复核状态见 `score/final-report-r12/`。
- 本包由 `tools/make_sample_pack.py` 确定性生成（同输入同字节）。
