# score/ 目录索引（判官批次与报告）

冻结口径与指纹以 `docs/data-card-v2.md` §1 为准；★ = 冻结/引用件，其余 = 过程批次
（保留供审计；历史都在 git，可按需精简）。

| 目录 | 角色 | 备注 |
|---|---|---|
| ★ `judge-full-3` | 第 3 全量轮（1674 次调用记录） | `records.jsonl` = `6e5d68e6…` |
| ★ `judge-full-3-replay` | 第 3 轮零调用回放 **= 冻结评分结果** | `results.json` = `afc8b73a…`（VM 逐字节一致） |
| ★ `judge-full-4` (+`-replay`) | A/A 稳定性第 2 轮 | 记录 `6acf7cd2…`；翻转 1.25%（决定性 0.12%） |
| ★ `host-prepare-r12` | R12 重判准备件（引用） | |
| ★ `final-report-r12` | **最终报告** | report `9a9d88d6…` / 雷达 `b6a5d279…`；与交付 b/e 同源 |
| `judge-full-1` (+`-replay`、`-replay-correctionA`) | 第 1 全量轮 + 极性纠正批 | 过程；17 个反判任务修复的证据链 |
| `judge-full-2` (+`-replay`) | 第 2 全量轮 | 过程 |
| `judge-pilot-1` (+`-replay`) | 试点小批 | 过程 |
| `host-prepare1/2/3` | 准备轮 | 过程，未进交付叙事 |
| `healed-scopedv2` | scoped-v2 口径评分 | 过程 |
| `robustness-1`、`robustness-prepare1`、`robustness-replay1` | 鲁棒性探针（小批） | 过程 |
| `final-report` | 旧报告（基于旧 plan sha） | 仅历史保留 |

- `*/records.jsonl` 为真实判官调用记录；`*-replay/` 为零调用回放（results 打分件）；
  每个回放目录带 `input-lock.json`（输入指纹锁，`tools/selfcheck_locked.py` 可校验）。
- 瘦身建议：无 ★ 的过程批次可从工作树删除、需要时从 git 恢复
  （`git checkout <commit> -- score/<目录>`）；**★ 件与 `*-replay` 不要删**。
