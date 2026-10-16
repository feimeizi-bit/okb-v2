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
| `score/` | 判官批次与最终报告（索引：`score/README.md`） | 证据归档 |
| `runs/` | 运行证据（552 任务冻结证据；索引：`runs/README.md`） | 证据归档 |
| `deliverables/` | 赛题交付 a–e（README + 五个交付物） | 提交件 |

## 复算链路（确定性，已验证 sha 复现）

```bash
python v2/02-generators/build_stock_v2.py     # 存量 68 题选举+修复（含两遍法 W207 注记）
python v2/02-generators/assemble_v2.py        # 92 题装配 → paper-v2 + 四层计划 → validate_plan
                                              #   含：判官口径归一化(R12) + plan-lint(E201/E202) 阻断级防线
python v2/02-generators/build_registry.py     # 断言库 registry（629 槽位）
python v2/02-generators/qc_v2.py              # 质检总检（红线1/3/4/5 + 历史失败模式对照）
# 作者稿有改动时先跑：python v2/02-generators/v2_split_plan.py <作者稿.yaml>…
```

## 当前状态快照（2026-10-09）

- **paper-v2.0**：92 题 = 存量修复 68 + 新著 24；sha256 `bca2c68c82be510f42b6bb90b24f719c0e086f627767107103dbabc9affb52fe`
- **计划**：`okb-four-layer-v1`，330 检查，`validate_plan` 通过（逐锚点 disposition 可溯源）
- **配额**：六维 15/15/15/15/15+17；withhold 10 / release 5；深化槽 时间 3 / 强扰动 3 / 多步 ≥4
- **lint**：0 error / 61 warning（W205 注记 ×52、W207 豁免 ×9）；**W206/W208/W212 = 0**
- **质检**：`v2/06-qc/qc-v2-report.md`；五档格 `expect-cells-v2.json`（92 格）
- **plan-lint**：0 error（E201 元注释/裸失败类、E202 absent 派生缺映射均为阻断级）
- **判官口径归一化（2026-10-13）**：94 条 semantic requirement 改写为「合规命题 + supported/contradicted 显式映射」（R12；台账 `v2/01-paper-v2/plan-requirement-normalization.json`）。
  背景：552 任务判官审计确认旧计划 17 个任务被极性反判判死（11 应 pass / 6 应 unassessable）、2 个假通过。
  **paper sha 不变**（只改计划层判官输入）。2026-10-13 已完成全量重判：`score/judge-full-3`（1674 次调用 0 失败）→ `score/judge-full-3-replay` → 新报告 `score/final-report-r12/`；验收：wifi 检查 6/6 翻转正确、17 个反判任务全部纠正。判官批次间仍有 12 个未改检查任务波动 → 需人工复核或走 multirun 多轮协议。
- **anchorcal**：死谓词 0、路径 0（对旧全局语料；新题待冒烟后复跑）

## 2026-10-13 追加（P0 交付面，全部离线可复算）

- **数据质量校准**：`v2/06-qc/item-stats-v2.{json,md}`（`tools/item_stats.py`）——
  92 题的难度/区分度台账（高区分 23 / 饱和 30 / 地板 9 / 重复波动 30），替代旧 QC 里未定义的"T1−T2"占位。
- **指标双口径 + 检查族**：`v2/06-qc/metric-bounds-v2.{json,md}` + `radar-six-dim-lower.{json,svg}`
  （`tools/metric_bounds.py`）——assessed-only 与 unassessable-按失败下界并列；失败按
  memory-store / artifact / reply / task-application 拆族（族名为启发式分流，见表注）。
- **样例包 b**：`v2/07-deliverable-b/`（`tools/make_sample_pack.py`）——3 个真实案例的
  任务+证据切片+逐检查解释+复现命令，同输入同字节。
- **方案补充**：`docs/benchmark-design-v2.md`——数据生成方式、覆盖与校准、评分口径、五类映射、创新点、限制。
- **workspace 自足（P0-5）**：补回 `okb/`（一键 CLI 分发器）与 `pyproject.toml`、旧冻结夹具
  （pilot-v0.2/judge-anchor-cases/four-layer-r1 等）；修 `tools/report.py` 3.11 兼容；
  修 `tools/portable/bundle.py` 离线入口在 Windows 上的 spawn 泄漏（platform.system/machine 回退到子进程）；
  `tools/tests` 全绿（872 passed / 63 skipped，skip 均带理由），`engine/tests` 42 passed。

## 2026-10-14 冻结（赛题线 v2.0-R12）

- **状态**：赛题线冻结；维护模式。**先读数据卡** [`docs/data-card-v2.md`](docs/data-card-v2.md)
  （冻结清单/口径/关键数字/覆盖矩阵/限制/v2.1 扩展计划/维护规则）。
- **交付物**：[`deliverables/`](deliverables/README.md) —— a 方案说明（含数据生成方式）、
  b 样例包（`v2/07-deliverable-b/`）、c 复现说明 + **openKylin 回执**（VM 链重建同 sha、
  评分重放与宿主逐字节一致、deb 安装可用）、d .deb 工具包、e 录屏脚本（待录制）。
- **冻结验证**：`v2/06-qc/final-verification.txt` —— 复算链 54 产物 0 漂移；
  `python -m pytest tools/tests engine/tests -q` → **921 passed / 63 skipped / 0 failed**
  （冻结时 914；新增 7 项为 B 环复核工具测试）。
- **字节约定**：`.gitattributes = * -text`、`core.autocrlf=false`；全仓 LF，
  仅 `paper-v2.plan.json` 保持 CRLF（判官记录绑定其 sha）。
  2026-10-13 的 autocrlf 检出事故已修复（run 目录 13101/13101 还原为锁定字节）。
- `build/` 已入 `.gitignore`（构建产物由卷 YAML 确定性重建）。
- 未完成项（不阻塞冻结，见数据卡 §5/§6）：录屏、双人 κ（B 环单裁 48 例已回收：
  人机一致 91.7%、0 假通过）、v2.1 扩展包（时间 2 题 + 敏感 2-3 题 + 深轮次 2-3 题）。
- **判官 A/A 稳定性（2026-10-13）**：同计划同请求再跑一轮 `score/judge-full-4`
  （1674/1674 成功），`tools/judge_aa.py` 报告：请求级翻转 **21/1674 = 1.25%**
  （决定性 supported↔contradicted 仅 2 条 = 0.12%；其余为 sufficient↔insufficient 弃权摆动），
  任务级变化 12/552 = 2.17% → 低于 3% 阈值，单批结论可引用，第三轮多数投票暂不触发。
  报告：`v2/06-qc/judge-aa-v2.{json,md}`。

## 边界

- 旧仓 `D:\project\okb-int` 与暂存 `D:\project\okb-holdings` 保留为**只读历史**，不再在其上推进 v2；
- 本工作区为独立 git 仓（已有提交；后续提交需用户口令）；
- VM 侧运行需重新同步本工作区的引擎+卷+计划。
