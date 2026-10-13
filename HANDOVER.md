# HANDOVER.md — okb-v2 工作区交接（2026-10-09）

> **给新会话（新 AI 会话/新协作者）**：本工作区自包含。开工前先读 **本文 + README.md**。
> 一切在本工作区演进；未经用户明确要求**不推送任何共享仓库**、不动旧协同目录。
> 花钱（Agent 采集、判官）必须先拿到用户金额批准；密钥只以环境变量名引用，不落盘不回显。

## 1. 项目与赛题速览

- **okb**：面向 openKylin 的智能体长期记忆自动化评测 Benchmark（比赛项目）。
- 六维：长期保持 / 记忆调用 / 动态更新 / 相近区分 / 边界识别 / 任务复用。
- 赛题权重：数据 25 / 自动评分 25 / 通用性 15 / 稳定复现 15 / 指标 10 / 创新 10。
- 交付：方案文档、样例任务+真实证据+评分、源码/配置/复现 README、一键 CLI（建议 .deb）、
  多维指标+雷达图、openKylin 桌面双 Agent 演示录屏（原文见 `docs/competition-task.md`）。
- 双被测 Agent：OpenClaw 2026.9.5 / Hermes v0.21.3（openKylin 3.0 VM，历史配置见 `docs/SETUP_VM_AGENTS.md`）。

## 2. 当前状态快照（2026-10-09）

| 资产 | 状态 | 指纹/位置 |
|---|---|---|
| **paper-v2.0 卷** | 92 题（存量修复 68 + 新著 24） | sha256 `bca2c68c82be510f42b6bb90b24f719c0e086f627767107103dbabc9affb52fe`，`v2/01-paper-v2/` |
| **四层计划** | 330 检查，`validate_plan` 通过、逐锚点 disposition 可溯源 | `v2/01-paper-v2/paper-v2.plan.json` |
| 配额 | 六维 15/15/15/15/15+17；withhold 10 / release 5；深化 时间3/强扰动3/多步≥4 | QC 报告 |
| lint | 0 error / 61 warning（W205×52 注记、W207×9 豁免）；**W206/W208/W212=0** | `v2/01-paper-v2/_paperv2-lint.txt` |
| 质检总检 | 红线1 C 读法全维达标；anchorcal 死谓词 0/路径 0；五档格已生成 | `v2/06-qc/qc-v2-report.md`、`expect-cells-v2.json` |
| 断言库 | registry 629 槽位（330 计划检查 + 299 锚点） | `v2/05-assertions/registry-v2.json` |
| 引擎副本 | okb-int @ `50c5251`（integration 工作树） | `engine/ tools/ schemas/ deploy/` |
| 真人标签 | **0**（两条回路都未回收） | 盲审包在桌面 + 暂存 |

## 3. 关键决策台账

**已定（继续执行）**
1. **自有链路线**：不再依赖旧密封验证包（其 pilot 回执门结构性不可达）；600 用仓内 `okb run` 链。
2. 规格口径：v1.1 修正案生效（红线2 对称硬门撤销、仅披露）；红线1 三读法公示、验收=C。
3. 出题硬检 8 条（见 `v2/01-paper-v2/sample-template.yaml` 头注释）：四形态计划层覆盖；
   ≥1 内容谓词判据；路径相对化；reply 无回声；时间真实性二选一；lint 0 error 且
   W206/W208/W212=0（W207 需书面豁免）；双读者自查；anchorcal 预检。
4. schema 硬规则：`expect.confusions` 只列规则可达形态（E120）；相位单调 `write<disturb<update<probe`（E103）；
   memory_entry 锚点须 `key/key_regex`（敏感值用 `key:"*"+value_regex`）；BR 题必填 `boundary_kind`；
   `suite_version` 形如 `v2.0`；计划 scope 规范 `memory_after/file_final(+path)/agent_reply(+session/turn)`。
5. trap 政策：入 `trap-ledger.json`，失败形态由计划层承接（不用 --expand-traps 的软锚点展开）。
6. 断言/标准迭代机制：**判官专用、按需触发**（设计见 `v2/05-assertions/assertion-iteration-design-v1.md` v1.1 修订）。
7. 切割：本工作区独立；旧仓 `okb-int`、暂存 `okb-holdings` 只读存档。

**待用户拍板**
- F1 审题范围（建议只审变更面 42 题）与人审排期（用户+同伴）；
- 冒烟与 600 的**金额口令**；判官金额上限；
- 判官选型确认（建议：主 `gpt-6.1-sol`（yuepa8 网关）/副 `deepseek-flash`；先发 2-3 条探针验模型名与计费桶）；
- 是否 `git init` 独立仓。

## 4. 复算链路（本机，确定性；已验证 sha 复现）

```powershell
cd D:\project\okb-v2
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
python v2/02-generators/build_stock_v2.py   # 存量选举+修复（两遍法 W207 注记；内置探测构建）
python v2/02-generators/assemble_v2.py      # 92 题装配 → paper-v2 + 计划 → validate_plan（应输出 sha bca2c68c…）
python v2/02-generators/build_registry.py   # 断言库
python v2/02-generators/qc_v2.py            # 质检总检 → 06-qc/
# 作者稿有改动时先：python v2/02-generators/v2_split_plan.py <作者稿>.yaml
# judge-bench 自测：python v2/05-assertions/bench/assertion_bench.py --selftest --out <新目录>
```

## 5. 待办与关键路径（按优先级）

1. **F1 变更面审题单（免费，立即可做）**：对 24 新题 + 18 修复题生成审题单
   （工具 `tools/make_f1_review.py` 在本工作区，支持 `--paper`/`--plan`/`--out-dir`；注意 v2 计划为
   独立 JSON，判据展示走 plan 层）。→ 用户+同伴人审 → 问题项闭环。
2. **2 题真机冒烟（需金额口令）**：从 v2 挑 DU + 强扰动各 1；验证引擎×v2 卷×四层计划全链路；
   同时把 `update_multi_001`/`boundary_pii_mask_001` 的 `source_invalid` 风险**冒烟确证**（证据表示域问题）。
   冒烟前先同步工作区到 VM；VM 加固与 embed-server 冷启动预检协议见 §8。
3. **600 采集（需金额口令）**：92 题 ×2 Agent ×3 轮 ≈ 552 任务（或补机制到 600）；串行约 16-20h；
   可选跨产品并行（同 run_id 双目录 + `merge_runs`）。
4. **判官准备与执行**：60 条试跑请求的 judge-prep 已备（见暂存 `judge-prep/`）；四件套待定
   （模型串/CNY 单价/token 计数口径/wire+effort）；执行门=预算合同+账本+`--authorize-paid-calls`。
5. **assertion-bench 首轮**：用户+同伴 38 项三态标签回收后跑（judge 候选选优）。
6. 收尾材料：报告/雷达/数据卡/.deb/录屏（参照 `docs/` 与旧交付要求）。

## 6. 纪律与红线（必须遵守）

- **不推送共享仓库**（除非用户明确要求）；提交纪律若启用，用仓内 `tools/gitcommit.py`（含 ghost 门）。
- 预算：先估价/先批准，后调用；不设硬闸的项目自担账户监控；判官走预算合同+账本。
- 失败批保留、不覆盖、零重试；失败后换新 run-id。
- 机器结果与真人标签的**揭示顺序**：先锁两份独立标签，后揭示机器/判官结果（当前标签=0）。
- 密钥：VM `~/.okb/relay.env` 有 `RELAY_BASE_URL/RELAY_API_KEY`——只引用变量名，值不外泄。
- 破坏性操作（删文件/回滚 VM 快照/重启服务）先征得用户同意。

## 7. 环境与资源

- **VM**：`openKylin-V3.0`（VBox headless）。启动：`& "D:\tools\VirtualBox\VBoxManage.exe" startvm "openKylin-V3.0" --type headless`；
  SSH：`ssh -i C:\Users\test\.ssh\okb_vm -p 2222 okb@127.0.0.1`；优雅关机 `sudo -n systemctl poweroff`。
- VM 内已有：密封验证包 `~/validation-2026-10-09/`、其 repo `~/okb-validation-20261009/`（3 批次证据）；
  VM 加固：linger=yes、sleep/suspend 目标已屏蔽、长跑用 `systemd-run --user`；
  **embed-server 冷启动预检**：先跑 `openclaw memory index --agent okb` 返回 rc=0 再启批次。
- **宿主暂存**：`D:\project\okb-holdings\2026-10-09-pilot\`（batch1-stopped / batch2-false-start / batch3-pilot /
  delivery 盲审包 / judge-prep / package-copy）。桌面有给两人（用户+同伴）的同一份盲审包 zip。
- 预算已用：试跑批次一 3,663,851 tokens + 批次三 4,958,558 tokens（OC 可见；Hermes 无 usage 上报）。

## 8. 已知问题与风险

1. `update_multi_001` / `boundary_pii_mask_001` 确定性 `source_invalid`（listing fingerprint mismatch，PII 表示域）→ 冒烟确证/修表示；
2. OC `memory forget` 偶发锁超时（~8-10%/题）→ env_error，纪律保留；
3. anchorcal 仅覆盖 12/92（新题无证据）→ 冒烟后对全卷复跑；
4. `unverified_negative ×2`（update_multi_001 A6/A7，=B 旧卷同类）→ 披露+F1 抽检；
5. Hermes 无 model/usage 上报 → 报告中如实 null；
6. W207×9 / W205×52 为注记级（豁免已写）；不追认旧成绩。

## 9. 历史（为什么切割）

旧协同（okb-int 仓库）存在：预注册与修正案版本冲突、密封包 pilot 门结构性不可达、卡源单点
（cards-r3 仅在旧 B 侧机器）、多批次事故（logind 会话清理 SIGTERM、embed-server 冷启动假活）等。
经用户决定：v2 全部工作切至本工作区，独立演进；旧目录只读，不做任何同步或推送。
更完整的重构设计见 `docs/b-line-rebuild-v1.md`。
