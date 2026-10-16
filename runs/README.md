# runs/ 目录索引（运行证据）

| 目录 | 角色 | 备注 |
|---|---|---|
| ★ `v2r3_20261010-healed/` | **冻结运行证据**（92 题 × 2 Agent = 552 任务） | 约 1.3 万个文件与回放批次的 `input-lock.json` 逐字节一致；`manifest.json` = `e2c2dde9…`、`evidence.jsonl` = `3852ba17…`。**禁止增删改任何文件**（判官记录绑定其字节；改名或换行都会破坏重放）。 |
| `mock-c2-v02-*/` | 历史：mock 考生基线（5 个 run） | v0.2 引擎自测的合成运行；对照用，不进交付叙事。 |

- 本目录**不在 VM 同步范围**（`deploy/sync-to-vm.ps1` 只带 engine/tools/samples/deploy/docs/notes/okb/deliveries）；
  缺失时相关测试按"跳过"处理（不报失败、不假装通过）。
- 读法：`evidence.jsonl` 逐行事件 → `raw/<task>/` 原文 → `manifest.json / tasks.jsonl / usage.json` 台账
  （口径见 `docs/data-card-v2.md` §6 与交付 a §6）。
