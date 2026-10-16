# openKylin 智能体长期记忆自动化评测 · 方案说明（面向评审）

版本 v1 · 2026-10-05 · 对应赛题交付物 a
本文只写**已经在盘上/仓库里成立**的东西；每一项后面都跟着它的证据位置。没做到的写在 §9，不混进正文。

---

## 1. 我们要评什么，判什么

长期记忆评测通常退化成"问一句、答一句、看关键词"。我们评的是**行为后果**：一个智能体在 openKylin 上
跑完一次真实任务之后，从它留下的证据能不能判定它**记住了什么、忘了什么、记成了什么、不该记的记了没、
该用的时候用对没有**。

五类结果（赛题明确要求可区分）：

| 类别 | 含义 | 我们的判据位置 |
|---|---|---|
| remembered 正确记忆 | 应保留的信息被保留并在行动/回复中正确使用 | 四层计划的强制检查集全过 |
| omitted 遗漏 | 该记住/该用上的没出现 | `file_exists` 客观不成立，或记忆语义检查判否 |
| confused 混淆 | 相近信息串了、旧值残留、把临时当长期 | 字段级/共现型语义检查 |
| wrongly_persisted 错误持久化 | 明确不该入库的进了记忆 | `memory_after` 上的 absent 型要求 |
| wrongly_reused 错误复用 | 拿历史值套到不该用的场合 | 产物层反值检查（与探测轮要求成对） |

六项能力沿用赛题口径：长期保持、记忆调用、动态更新、相近区分、边界识别、任务复用。

---

## 2. 一句话能跑的主链路

```bash
okb run --agents deploy/agents.yaml --paper build/paper-r1.jsonl \
        --plan samples/four-layer-r1.json --run-id <id> --repeat 3 --verify-traces
```

一条命令依次完成：**运行 → 证据采集 → 分层自动评分 → 诊断报告 → 六维雷达**，阶段彼此隔离，
任一步失败都记进 `build/run-<id>/run-summary.json`，前面已产出的产物不丢。
CLI 共 22 个子命令（`okb --help`），全部薄转发到 `tools/`，打包后入口 `/usr/bin/okb`、工作目录 `/opt/okb`。

---

## 3. 基本前提（我们把什么当成事实）

1. **证据先于结论**：评分只看被采到的证据——对话、持久化记忆快照、行动轨迹、文件产物。没采到就是没采到，
   不推断。
2. **"评不出来"不是"零分"**：任务结局与错误归因分成两条轴。评测器自身出问题（预检失败、能力不满足、
   证据自相矛盾）记 `unassessable`；只有证据完整且不成立才记 `fail`。这条区分是长期记忆评测里最容易
   被糊掉的地方——一旦糊掉，工具坏掉的样子和智能体失败的样子长得一样。
3. **判据不靠关键词命中**：卡片包自己写的允许等价表述规则（"语义等价、换语言、重排结构均允许，
   不使用禁词或字符串出现即判错"）被我们当成硬约束：判据文本进语义层，由判官按证据判断。
4. **判据不许中途改写**：判据文本变了，请求指纹就变，旧答案对新题面失效（`row_key` 绑定 `request_sha256`）。
5. **可复算优先**：试卷、计划、覆盖矩阵都由编译器产出，删除后重跑逐字节一致（实测：`samples/paper-r1.yaml`
   `f30541ae…`、`samples/four-layer-r1.json` `5bed443d…`、`build/paper-r1.jsonl` `0da83755…` 两次重跑一致，
   且 Windows 与 Linux 检出一致）。手工改过的题目文件无法这样验证，所以我们不做手工卷子。
   一处**边界要说清**：`.deb` 容器自身的 sha 每次构建都不同（`dpkg-deb` 内部 gzip 头带当前时间戳，
   已试 `touch` 固定文件 mtime 仍不可复现）。因此包的可复现性声明只覆盖**内容**：两次构建解包后
   `diff -r` 为 0 行、控制信息一致、包内试卷/计划指纹与仓库逐字节相同（212 个文件实测）。

---

## 4. 数据集设计（交付要求里的"可扩展数据结构"）

### 4.1 结构分层
```
候选卡 cards-r3（100 张，含要求/作用域/依据轮次/拟判失败类别/时间语义）
   │  tools/freeze_candidates.py  ← 闸序 + 原因码 + 结构锚点 + 无损搬运
   ├─→ samples/paper-r1.yaml      （题：脚本 / fixture / 锚点 / 期望 / 计分策略）
   │      │ tools/build_paper.py（编译 + schema 与 lint 校验）
   │      └─→ build/paper-r1.jsonl      引擎读的试卷，sha256 即试卷身份
   └─→ samples/four-layer-r1.json（四层评分计划：每题的强制检查集 + 锚点归属）
          绑定 paper_sha256：换卷即换计划，不能错配
```
一张卡要么**成题**，要么带**原因码**留在台账里；不存在"悄悄丢掉一些题"。

### 4.2 当前卷子的组成（实测计数）
100 题 = 75 张编译卡 + 20 条 pilot-v0.2 + 5 条 batchA（后两者文本级原样并入）。
计划 100 行 / **295 项强制检查** / 257 个锚点 / 22 个初始工件。

| 维度 | plan 行 | 其中卡片自带 |
|---|---|---|
| 长期保持 | 20 | 17 |
| 记忆调用 | 18 | 16 |
| 动态更新 | 5 | **0**（17 张卡全在这一维，见 §9.5） |
| 相近区分 | 22 | 16 |
| 边界识别 | 20 | 15 |
| 任务复用 | 15 | 11 |

四类证据来源的判据分布：`memory_after 93 / agent_reply 82 / file_final 69`；`memory_before 0`（§9.6）。
按失败类别计的强制检查（同一检查只归一类）：`omitted 215 / confused 54 / wrongly_reused 20 / wrongly_persisted 6`。

### 4.3 可扩展性（怎么从 100 涨到更多，而不牺牲可信）
`--count / --per-dimension / --min-per-dimension / --max-per-family / --allow-time-mode /
--allow-restart / --target-samples` 全部是参数；闸序固定、排序按（六维枚举序, 卡 id）与"哪款
智能体得分低"无关，因此扩题不会变成反向拟合。**当前瓶颈不是流水线，是判据**：100 张卡里
只有 75 张进卷（实测原因：17 张卡因 `update` 相位落在试卷三相枚举之外被拒、8 张被总量配额裁掉；即 100 张中 83 张能通过全部闸，其中 75 张按确定性排序被选中），要再扩必须由人补写
可独立读懂的判据，见 §9。

- **编译器分工**（本轮拆分，单文件 1189 行 → 660 行 + 三个职责单一的模块）：
  `tools/freeze_gates.py`（卡片→题卷的闸序与编译，判据所在地）、
  `tools/freeze_derive.py`（锚点→四层检查项的机械派生）、`tools/freeze_lint.py`（题面红旗 lint，只报告不改写）、
  `tools/freeze_candidates.py`（装配 + 矩阵 + CLI）。入口命令与产物**不变**；
  拆分的验收是 `tools/tests/test_paper_r1_reproducible.py`：重算交付卷必须逐字节等于
  `f30541ae… / 0da83755… / 5bed443d…`（这条测试确实抓住过一次我引入的不可复现回归）。

### 4.4 "长期"这件事我们用什么承载
不用挂钟等 24 小时当主手段（一测就是半天、还测不到系统行为）。用**系统级边界**：跨会话隔离、
进程重启、初始工件与末态产物的字节比对、记忆面快照对比——这些正是 openKylin 这类可控环境
才能提供的观测面，纯对话式方案拿不到。

---

### 4.5 基线/准入题族（`paper-r2` **候选卷**，r1 仍是交付卷）

**发现的缺口（实测）**：`samples/four-layer-r1.json` 的检查项作用域是
`memory_after 93 / agent_reply 82 / file_final 69 / objective 工件类 51（这类检查项按 schema 不带 scope） / **memory_before 0**`。
没有任务前基线断言时，"这一轮没记住"和"这条从来没被写进记忆"在评分上塌成同一个 `omitted`——
而这两件事对记忆系统的诊断意义**相反**（保留能力失效 vs 写入/准入能力失效）。

**修法不碰契约**：题卡 schema 早已有 `evaluate_at: snapshot_before`（规则评分器
`tools/scoring/anchors.py` 消费它；采集侧 `engine/runner.py` 每个任务前真的拍一次 before 快照）。
缺的只是我们的派生器把 `memory_entry` 一律映射成 `memory_after`。修好后（
`tools/freeze_candidates.py:derive_pilot_checks`），自研 4 张基线卡即可产出 `memory_before` 检查项。

**r2 = r1 的 100 题逐字原样 + 4 张自研基线卡**（`tools/extend_paper_r2.py` 可重跑，删除后重建一致）：

| 项 | 实测值 |
|---|---|
| 题数 / 计划行 | 104 / 104 |
| 检查项作用域 | `memory_after 97 / agent_reply 86 / file_final 69 / objective 51 / **memory_before 4**` |
| 计划校验 | 用评分侧自己的 `validate_plan()` 通过（不是我们自写的断言） |
| 真实跑批 | `okb run` 104 题 × 1 mock × 2 轮 = **208 任务 / 45s**，`evidence_valid 204` |
| 基线证据是否真的分开 | 同一任务下 `memory_before` → `E1 (phase=before)`、`memory_after` → `E6 (phase=after)`，**不同证据对象** |
| r1 是否被动过 | 否：`f30541ae… / 0da83755… / 5bed443d…` 三份指纹逐字不变（`r1_untouched: true`） |
| 凭据 | `notes/paper-r2-candidate.json`（由脚本生成，审计器会重算比对） |

每题完整字段（题面原文、锚点、限制）见一页纸 [docs/paper-r2-cards.md](paper-r2-cards.md)（由 `samples/paper-r2-extra.yaml` + 计划 + 凭据渲染，不手抄）。

**4 张卡各管一件事**（判据只取题卡自述文本，不为凑覆盖发明关键词；失败类只用既有四个枚举）：

| 卡 | 维度 | 基线断言 | 违反时归类 |
|---|---|---|---|
| `baseline_no_prewritten_order_001` | long_term_retention | 任务前不得已含本任务要写入的验收顺序 | `wrongly_persisted` |
| `baseline_clean_slate_ticket_001` | task_reuse | 任务前不得已含上一任务的合成工单号 | `wrongly_persisted` |
| `baseline_no_prior_preference_001` | similar_discrimination | 任务前不得已存在被否定的邻项（拿铁） | `confused` |
| `baseline_no_reuse_of_other_task_001` | task_reuse | 任务前不得已含其它任务的会议时间 | `wrongly_reused` |

**必须一起读的限制**（写在卡片 `meta.limitation` 里，不在正文里藏）：基线非空既可能是真实的
跨任务残留，也可能是评测环境没重置记忆面——两种解释都要保留。第 4 张卡依赖同族配套任务
**先于它执行**，否则基线断言恒真、不提供信号。定类优先级按 CONTRACT §5 首条命中：锚点顺序
A1→A2→A3，所以"既没记住又基线污染"会先判 `omitted`，只有召回成功而基线被污染才判更重类。

**配套的读数仪器**：`okb baseline-attribution`（`tools/baseline_attribution.py`），并已接进报告层（`okb report-vnext` 独立一节「写入归因」；集成凭据 `notes/report-attribution-integration.json`）。它是**诊断量，不计入六维与排名**：`dimensions` 与雷达仍是六个能力读数，加入这一节后`overall_score` 依旧 `null`、`release_authorized` 依旧 `false`——有对照测试逐项比较带/不带归因的报告，要求所有评分字段完全相同。它只用三个
不依赖正文匹配的可观测量（任务前快照条目数、本题写入事件数、任务后快照条目数），把 `omitted`
拆成五个状态：`write_attributed / never_written / residue_plus_write / residue_cleared / capture_gap`
（第五态 = 看到写入事件但任务后记忆面读到 0 条目，两个观测互相矛盾 ⇒ **无法归因**，既不算能力象限
也不算智能体失败；我第一版把这种情况圆成了 `write_attributed`，现已由测试钉住）。判定只看**记忆面终态**，写入事件只用于发现仪器矛盾。旗标进入 `review_queue`，每条带 `suggested_check` 与 `counts_toward_score=false`，不产生任何分数。

- **在真实 r2 证据上的首个读数（离线 mock，104 题）**：4 道基线题全部判为 `never_written`
  （`never_written_share = 1.0`）——四层评分只会说"没通过"，这台仪器说得出
  "记忆面里从来没有过"，也就是**写入/准入失效**而不是保留失效。这正是 r1 无法报告的区分。
- 刻意**不做**的事都写在 `limits` 里并各有测试：不做关键词匹配（源码级静态守卫，连 `import re`
  都不允许）、没有基线题族的计划直接拒绝运行、缺成对快照的任务单列计数而不是当成 0 条目。
- 每个状态与每类旗标都有可达测试（`tools/tests/test_baseline_attribution.py`，12 项），并已纳入安装态
  自检的关键链测试清单。

**为什么 r1 仍是交付卷**：切换交付卷会让 A 侧已按 r1 签发的预登记（`paper_sha256`）失效、需重登记
重跑。因此 r2 作为候选存在，`okb register-run` 已能在 r2 上自校验通过；**取代与否是队伍决定，
不是技术既成事实**（§9 仍把"六维偏科"列为未取得）。

## 5. 结果收集：证据与可追溯性

- 统一采集器落 `evidence.jsonl` + 逐条 raw 归档（显式 UTF-8/LF，跨平台字节确定）
- 每条判据都能追到原始证据：`report/index.html` 里每个检查项带引文，`--verify-traces` 离线重建
  "检查项 → 请求 → 引文 → 原始 raw 文件"，防止把派生文件状态当成原文
- 记忆证据分档（不是一票否决）：`T0 缺失/异常`（仍不可判）、`T1 仅声明快照`、`T2 快照已绑定观测回执
  或全文采集`、`T3 回执完整且全文表示已验证`。**任何一档都不等于对记忆存储做过带外独立验证**，
  这句话在报告与雷达图上都印着；`T1` 支撑的结论自动进人工复核队列
- 产物来源表示也被记账：历史 raw 里"指纹字段是采集器补写前的占位值"这种老数据，
  按 `legacy_pre_fingerprint_raw` 标注后仍可评分，但**不返写历史字节**

---

## 6. 自动评分流程（交付要求里的"少人工介入 + 可解释原因"）

四层，逐要求判定，输出三态 + 理由 + 引文：

1. **证据门**（分层，见 §5）：作用域内证据不足以支持任何结论 → 该题不可判，并给原因码
2. **客观层**：文件存在性 / 未改动 / JSON 字段等值。只用机械判据，不掺正则猜测
3. **语义层**：每个要求一次判官调用，输入只有"要求 + 任务脚本上下文 + 作用域内观测"，
   输出必须是严格 JSON `{result, rationale, citations}`；**引文的 quote 必须逐字存在于所指证据里**，
   否则整条答案作废（不是降级，是作废——这道门让我们能拒绝"听着合理但证据里没有"的判定）
4. **复核层**：违反哪条检查、哪些没判出来、低档证据、与旧口径不一致，全部落 review-queue

工程口径：默认 `prepare`（0 次 HTTP、0 费用，语义项只出待判请求）；`--replay` 精确回放（请求指纹
必须逐字相等，多出来的答案记录会被拒绝）；`--live` 需显式授权与预算/记账文件。三态结果与任务结局
解耦，`assessed` 在 prepare 下只能来自客观层——报告里明说，不让人误读成"判官已经判过了"。

判官可替换且被当成被测对象：本次用 `gpt-6-sol`（网关）与 `deepseek-flash`（官方直连）对**同一批
逐字节相同的请求**各答一遍，一致性见 §8。

---

## 7. 智能体接入的通用性

| 适配器 | 状态 | 说明 |
|---|---|---|
| OpenClaw | 已用真实历史证据跑通 | 从 CLI JSON meta 读 model/usage |
| Hermes | 已接入 | `-Q` 不报 model/usage，因此 usage 一律 `None`（不写 0 冒充观测值） |
| mock 合成考生 | 已接入 | §14 独立子进程，用于链路自检与判官可靠性测量 |
| `cli_template` 声明式适配器 | 已接入并实测 | 第三方智能体**不改代码**，靠 `deploy/agents.yaml` 声明命令模板与字段路径 |

声明式适配器的设计取舍值得强调：赛题点名的 KylinBot / kylin-agent 我们**没有它们的 argv 取证**，
所以没有写一个"猜出来的 kylinbot.py"——那会在 VM 上跑出一片 `env_error`，还把"已支持"变成虚报。
改为把"怎么调用一个 CLI"外置成配置：对话文本一律走 stdin（不进 argv，防注入与超长）、占位符白名单
校验（未知占位符在启动子进程前就报错）、抽不到 model/usage 就 `None`、模板里的凭据在写 manifest 前
脱敏（`--token <value>` 这种位置掩码）。

能力声明不足时调度器**主动跳过**该题（实测：声明 `file:false` 时 36 题被如实 skip，而不是硬跑出假
结果）。这条"宁可少跑也不造假"的口径，同样用于人审标注（判官答案封存）。

---

## 8. 可信度证据（实测，含一个坏消息和一个负结果）

| 项 | 实测 | 证据 |
|---|---|---|
| 真实智能体运行证据上自动出判定 | 4/4 题证据有效、4/4 出结局：pass 3 / fail 1，类别 remembered 3 / omitted 1；两个判官各自独立都得同样结论 | `build/run-real_d1b/` |
| 题间区分度 | `dual_order_001` 跨智能体差值 **1.0**（Hermes 通过、OpenClaw 遗漏安静座位偏好）；`retain_allergy_001` 饱和（双通过） | `okb discriminability` |
| 重复评分稳定性 | 同一输入两次评分，`results.json` **字节相同** | `build/kappa/judge1-repeat/` |
| 判官 ↔ 判官一致性 | 60 条分层抽样（少数类全收）得 45 对可比：**κ=0.4727**，Po 0.667，Pe 0.368，精确 95% 单侧下界 **0.534**；`wrongly_reused` 最差 6/12 | `_review/four-layer/judge-batch-2026-10-04/agreement.json` |
| 判官输出契约合规 | gpt-6-sol 4/60 被严格引文校验拒收；deepseek-flash 12/60 非严格 JSON（思考耗尽输出预算） | 同上 |
| 早期小样本假象的纠正 | 14 对时 κ=1.0；扩到 45 对后 0.47。**我们把它写进文档而不是只留好看的数** | `_review/four-layer/dual-judge-kappa-2026-10-04.json` |
| 负结果（自我检验） | 我的假设"判据里的作者速记/内部编号导致判官分歧"被数据否定：含标记题 22/30 vs 干净题 8/15，Fisher 双侧 p=0.2004 | `rubric-quality-vs-agreement-2026-10-04.json` |
| 判据可读性缺口 | 111/244 语义判据带机器可检的写法问题（短、内部裁决编号、`=wp` 速记），已入矩阵并出改写清单，**本轮不改写**以免测量不可比 | `notes/paper-r1-coverage-matrix.json` → `gaps.rubric_quality` |
| 工程质量 | 71 套件（70 Python + 1 JS）/ 895 用例 / 0 失败；含"不许编造正则""未答项不算判官答案""跨包答案冲突即拒绝""题面必须带作用域"等负面用例 | `engine/tests/`、`tools/tests/` |

---

### 8.3 跨轮归因（稳定性 15% 的仪器自证）
`okb report-multirun` 的 `report.json` 现在带 `attribution` 节：逐批次归因 + 聚合
（`tools/baseline_attribution.aggregate_reports`）。它回答一个平时没人问的问题——
**跨轮稳定性读数在什么条件下是假的**：

- `per_batch_trend` 给出每轮的残留数。若第 1 轮干净、第 2/3 轮出现 `residue_*`，
  说明轮间记忆面没被真正隔离，那么"三轮一致"衡量的其实是**污染的一致性**，不是稳定性；
- 聚合只做整数求和 + 比率重算（`never_written_share = Σnever_written / Σn`），
  **绝不对各轮比率取平均**；`n=0` 时比率是 `null` 而不是 `0.0`；
- 计划不含基线族时逐批降级为 `not_applicable` 并保留原因，`attribution_counts={}`——
  降级不等于"干净"（有真实链路测试断言这一点）；
- 该节不改变 `dimensions / tasks / attempt_history / overall_score`（依旧 `null`）。

### 8.4 两条人审回路（互相不可替代）

| 回路 | 人在判什么 | 命令 | 产出 |
|---|---|---|---|
| **判据回路** | 判官对某条要求的结论对不对（三态：supported / contradicted / insufficient） | `okb annotate-checks make-worklist / validate / agreement` | 人 vs 人 κ、**人 vs 判官 κ**、混淆矩阵、分歧清单 |
| **仪器回路**（本轮新增） | 这条**观测组合**最可能的解释是什么（四种：智能体没写 / 观测面漏扫 / 别任务残留 / 证据不足） | `okb annotate-checks make-harness-worklist --attribution <归因报告> [--run-dir <跑批目录>]` → `harness-agreement` | 人 vs 人 κ + **人工共识 vs 仪器旗标一致率**（含精确 95% 下界） |

仪器回路的三条设计约束（都有测试钉住）：
1. **题面只给事实**（前/后快照条目数、写入事件与两侧扫描覆盖范围），结论字段
   （`attribution/flags/suggested_check/counts_toward_score/instrument_label`）一律不进题面；
   失明按**结构**判定，不对整行做子串匹配（样本 id 里带 "residue" 这类字样时子串检查会假红，
   我第一版就这样被自己的测试数据骗过一次）。
2. **仪器的主张封在 `flags.private.json`**，双方交卷（`state=closed`）之前不参与计算。
3. 一切正常的行（`write_attributed`）**不入池**，否则一致率会被平凡行灌水；
   两人都选 `insufficient` 不算答案，不进"与仪器一致"的分母。

为什么值得做第二条：判官回路回答"自动评分像不像人"，仪器回路回答"**我们自己造的量是不是在量它声称的东西**"。
前者不能替代后者——一个与人高度一致的仪器，也可能在测一个空的东西。

## 9. 还没做到的（明确列出）

1. **openKylin 真机**：`.deb` 安装与运行只在 WSL2 Ubuntu 24.04.3 上实测通过（当前交付包的大小/sha256/成员数由 `tools/make_deb_appendix_facts.py`
   量入 `deploy/RUN_ON_OPENKYLIN-appendix.md` 的 DEB-FACTS 表，本文不抄数字，解包内容含 100 题卷 `f30541ae…` 与计划 `5bed443d…`，自检测的就是这份交付卷（100 题 × 单 mock agent × 2 轮 = 200 任务，含预登记与跨轮汇总）；`dpkg -i` rc=0、
   `ii okb 0.1.0`、安装态自检 **24 项全绿（pass=24 fail=0）**、`dpkg -V` 干净）。真机与 `lintian`、软件源收录未验证。
   更正一处早期记录：本节先前引用的是 100 题卷生成**之前**构建的包（解包核对后发现里面是 39 题版），
   原因是我拿错了比对基准、把"应该的值"当成了"实际的验证"。现已按当前树重建，并且只认解包后的
   包内指纹（不引用中间构建的容器 sha，避免文档里留下无法核对的历史噪声）。
2. **真实环境的两类缺口**：真机上真实双智能体的跑批（600 任务口径 = 100 题 × 2 Agent × 3 轮，
   两端均预判 100/100 可跑）；交付 e 的桌面录屏未拍。
3. **真人标签为 0**：所以"自动评分可信"目前只有仪器间证据（κ=0.47），缺人 vs 判官锚定；
   第二条（仪器）回路的题包与命令已就绪并通过测试，同样**还没有人填过一行**。
   标注包与规则已就绪（共标 30 + 各加标 20×2，密封、交卷后才揭示）。
4. **第三/第四智能体取证**：KylinBot / kylin-agent 的实际 argv 与记忆导出格式未拿到；拿到即可
   用配置接入，不需改代码。
5. **`dynamic_update` 维度**：17 张被拒卡实测**全部**落在这一维（`update` 相位）（映射成 `write` 触发 E103
   单调性，映射成 `disturb` 与语义相反），当前该维判据全靠并入题源；扩枚举属 CONTRACT 层决定。
6. **`memory_before` 证据类**为 0：没有任何检查声称做过带外的"任务前状态"验证。
7. **18 个人工锚点无强制检查集**（未声明 `on_violation` 或属事件类锚点，四层作用域无法表达），
   保留为 diagnostic 位并逐因记账。
8. **发布合规**：仓库根目录无 LICENSE / `debian/copyright`；按既有约定不凭空生成许可文本，
   属发布阻断项，需权利人确认。
9. **历史证据文件已在库内**：`runs/` 下 1482 个文件在 `.gitignore` 生效前就已被跟踪（实测 `git ls-files` 统计，均为 9 月 A 侧提交引入），里面含原始请求/回复文本。发布前要不要剔除需要决定：它们是交付物 b 的实例证据，但仓库注释自己写着"含原始 LLM 请求，可能带 API key — 禁止入库"。当前实测两个 API key 在全部跟踪文件里出现 0 次。
10. **跨平台一致性残余风险**：工作副本里 551 个跟踪文件带 CRLF（`text=auto` 检出转换所致）；
   打包时已净化 `control`/`postinst`，但经 scp 直推工作副本到 VM 的路径仍可能带回 CRLF。

---

## 10. 与评审维度对照

| 维度 | 权重 | 本文哪里在答 | 实测/未取得 |
|---|---|---|---|
| 任务定义与通用性 | 15% | §7 四种适配器 + 声明式接入 | 实测：配置接入的第三方跑通全链；未取得：真 KylinBot |
| 数据设计质量 | 25% | §4 结构、六维/四类证据/五类结果、可复算编译 | 实测：100 题 295 检查；未取得：61 题人工判据 |
| 自动评分能力 | 25% | §5–§6 分层证据门 + 逐要求判定 + 引文逐字校验 + §8 κ | 实测：真证据 4/4 判定、κ=0.47；未取得：人 vs 判官 |
| 稳定性与可复现 | 15% | §3.5 可复算 + §8 重复评分字节一致 + 多轮预登记汇总 | 实测：Linux/Windows 字节一致；未取得：真机多轮 |
| 指标完整性 | 10% | §5/§6 多维指标、缺失不填 0、误差带 | 实测：六维雷达 + 缺失语义 + 分维计数 |
| 创新性与工程落地 | 10% | §4.4 系统级长期边界 + §2 一键 + `.deb` + 70 套件全绿 | 实测：安装态 22/0（Ubuntu 24.04.3 代理环境，含交付卷与预登记）；未取得：真机、录屏 |

---

## 附录 A：内部编号对照
项目内部使用 S0–S10（工作线）与 G0–G4（闸门）编号，历史文档中的批次号（batch1–batch19）指同一
批工程记录；本文一律不引用这些编号，只引用盘上产物路径与实测数字。私有审计材料位于仓库之外
（`_review/four-layer/`），不进发布包。

## 附录 B：复现入口
```bash
bash deploy/build-deb.sh && sudo dpkg -i dist/okb_0.1.0_all.deb
OKB_ROOT=/opt/okb bash /opt/okb/deploy/openkylin-verify.sh    # 24 项离线自检
python3 tools/build_paper.py samples/paper-r1.yaml -o build/paper-r1.jsonl   # 期望 0da83755…
```

> **口径补充（2026-10-05）**：包事实（大小/sha256/成员数）不再手抄进本文——> 它由 `tools/make_deb_appendix_facts.py` 从"包 + 成员旁证 + 自检回执"生成到附录的> `DEB-FACTS` 标记区，并有测试保证"表 == 重算"。原因是本轮又出现一次抄旧了的情况。
>
> `.deb` 的容器 sha256 与构建实例绑定（`tar` 内 mtime/gzip 头），
> 因此它只用于**交接核对**（本文所列为 `dist/okb_0.1.0_all.deb` 见附录 DEB-FACTS 表，
> 已在 Ubuntu 24.04.3 实测 `dpkg -i` + `dpkg -V` 无差异 + 安装态自检 24 项全绿，
> 回执见 `notes/install-state-selfcheck-2026-10-05.json`）；可复现的是**包内容**，
> 不是容器字节。这是代理环境（Debian 系）证据，**不能替代 openKylin 真机验证**。
