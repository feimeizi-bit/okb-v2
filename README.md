# okb-v2 独立工作区

> 2026-10-09 创建。把 v2 的全部工作（引擎 + 试卷 + 判据/质检/断言工具）从旧协同中整体切出，
> 独立演进。**不推送旧仓库，不依赖旧协同的任何运行状态。**

## 目录

| 路径 | 内容 | 来源/性质 |
|---|---|---|
| `engine/ tools/ schemas/ deploy/` | 评测引擎、构建/校验/评分工具、schema、部署配置 | 从 `okb-int @ 50c5251`（integration 工作树）复制 |
| `docs/` | 赛题原文整理、CONTRACT、规格 v1.1、搭机记录、四层评分、盲审协议、B 线重构方案 | 精选复制 |
| `samples/paper-r1.yaml` | 旧卷（遗留输入，只读） | sha `f30541ae…` |
| `samples/four-layer-r1-dev2.json` | 旧计划（存量修复的检查来源，只读） | sha `a96e5bbf…` |
| `notes/anchorcal-paper-r1.json` | 旧锚点校准凭据（路径风险清单来源） | 只读 |
| `v2/` | **v2 工作区**（作者稿/装配/质检/断言库） | 本工作区自有 |
| `v2/01-paper-v2/` | 部件作者稿 + 修复台账 + **paper-v2.yaml / paper-v2.plan.json（正式件）** | — |
| `v2/00-wp0/` | 旧卷逐题质检分级（WP0）产物 | — |
| `v2/05-assertions/` | 断言库 registry + judge-bench 骨架 + 迭代机制设计 | — |
| `v2/06-qc/` | 质检报告 + 五档预期格（expect-cells-v2.json） | — |
| `v2/02-generators/` | 全部生成器（可复算链路） | — |

## 复算链路（确定性，已验证 sha 复现）

```bash
python v2/02-generators/build_stock_v2.py     # 存量 68 题选举+修复（含两遍法 W207 注记）
python v2/02-generators/assemble_v2.py        # 92 题装配 → paper-v2 + 四层计划 → validate_plan
python v2/02-generators/build_registry.py     # 断言库 registry（629 槽位）
python v2/02-generators/qc_v2.py              # 质检总检（红线1/3/4 + 历史失败模式对照）
# 作者稿有改动时先跑：python v2/02-generators/v2_split_plan.py <作者稿.yaml>…
```

## 当前状态快照（2026-10-09）

- **paper-v2.0**：92 题 = 存量修复 68 + 新著 24；sha256 `bca2c68c82be510f42b6bb90b24f719c0e086f627767107103dbabc9affb52fe`
- **计划**：`okb-four-layer-v1`，330 检查，`validate_plan` 通过（逐锚点 disposition 可溯源）
- **配额**：六维 15/15/15/15/15+17；withhold 10 / release 5；深化槽 时间 3 / 强扰动 3 / 多步 ≥4
- **lint**：0 error / 61 warning（W205 注记 ×52、W207 豁免 ×9）；**W206/W208/W212 = 0**
- **质检**：`v2/06-qc/qc-v2-report.md`；五档格 `expect-cells-v2.json`（92 格）
- **anchorcal**：死谓词 0、路径 0（对旧全局语料；新题待冒烟后复跑）

## 边界

- 旧仓 `D:\project\okb-int` 与暂存 `D:\project\okb-holdings` 保留为**只读历史**，不再在其上推进 v2；
- 本工作区尚未 `git init`（如需独立仓，一条命令即可）；
- VM 侧运行需重新同步本工作区的引擎+卷+计划。
