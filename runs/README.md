# runs/ 目录索引（运行证据）

| 目录 | 角色 |
|---|---|
| ★ `v2r3_20261010-healed/` | **冻结运行证据**（92 题 × 2 Agent = 552 任务，约 1.3 万个文件）：与评分回放批次的 `input-lock.json` 逐字节一致；`manifest.json` = `e2c2dde9…`、`evidence.jsonl` = `3852ba17…`。**禁止增删改任何文件**（判官记录绑定其字节）。 |
| `mock-c2-v02-*/`（5 个） | 合成考生基线：**判官漂移校准的测试夹具**（`tools/tests/test_judge_drift.py` 离线运行所需的历史证据），请勿删除。 |

- 读法：`evidence.jsonl` 逐行事件 → `raw/<task>/` 原文 → `manifest.json / tasks.jsonl / usage.json` 台账
  （口径见 `docs/data-card-v2.md` §6 与交付 a §6）。
- 其余历史运行批次已从提交树清理，完整历史见仓库分支 `v2-eval-20261013`。
