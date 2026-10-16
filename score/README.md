# score/ 目录索引（判官批次与报告 · 提交版）

| 目录 | 角色 | 指纹 |
|---|---|---|
| `judge-full-3` | 全量判官调用记录（1674 次） | `records.jsonl` = `6e5d68e6…` |
| `judge-full-3-replay` | 零调用回放 **= 冻结评分结果** | `results.json` = `afc8b73a…` |
| `judge-full-4` (+`-replay`) | A/A 稳定性第 2 轮 | `records.jsonl` = `6acf7cd2…` |
| `host-prepare-r12` | R12 重判准备件 | — |
| `final-report-r12` | 最终报告（report + 六维雷达 SVG/JSON） | `report.json` = `9a9d88d6…` |

- `*/records.jsonl` 为真实判官调用记录；`*-replay/` 为零调用回放（打分件，带 `input-lock.json` 输入指纹锁）。
- 回放命令与期望值见 `deliverables/c-复现说明.md`；过程批次（第 1/2 全量轮、试点、准备轮、鲁棒性探针等）
  已从提交树清理，完整历史见仓库分支 `v2-eval-20261013`。
