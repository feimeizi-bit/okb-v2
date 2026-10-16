# CONTRACT.md — okb 接口与执行语义契约

> 状态：**v0.1 已冻结（2026-09-22）** —— B 起草；A 逐条确认（`docs/contract-a-confirm-v0.1.md`）；B 抄回 v0.4 结论与 `absent × 历史叙述` 裁决；冻结前双轮对抗审计（momus/oracle）修正；**A 回签完成：`docs/contract-countersign-v0.1.md`「A 回复」CR-1~9 · NP-1~4 · Q1~14 全 ✅，3 备注已由 B 吸收**。自本日起适用 §10：只增不改。
> 权威性：字段与枚举以本文件为唯一权威。**冻结后只允许新增字段，不允许改名或删除**（见 §10）。
> 来源：`schemas/sample.schema.json` v0.3 + 三方评审 + 接口三轮往返（v0.2→v0.4）+ A 侧 §12 八项确认 + 冻结前双轮对抗审计 + C-2 实证反哺（E120/E121/W209/W210 入 §5 表 23~26 行）

本文件解决的是**schema 管不了、但实现者必须知道才能跑对**的事情。schema 管"字段长什么样"，本文件管"引擎拿到它该怎么动"。

---

## 0. 术语表（统一 5 种叫法，此前文档里有 5 种写法混用）

| 词 | 含义 |
|---|---|
| **suite** | 整套数据集（含版本号，如 `v0.1`） |
| **paper** | 一次评测使用的题目集合文件（`paper.jsonl`） |
| **sample** | 单条题目（`paper.jsonl` 的一行） |
| **task** | 某款 Agent 答某道题的一次执行（run 目录里的最小单位） |
| **run** | 一次批量运行，产生一个 `run_id`，含多款 Agent × 多道题的多个 task |

---

## 1. task 生命周期次序（必须按此顺序，否则证据链断裂）

```
reset(task 级：记忆 + workspace)
  → 布置 fixture（含创建父目录；占位符在此物化）
  → 【前置记忆快照】← 这是本 task 的基线
  → 按 script 顺序逐会话执行（每会话边界重置对话上下文）
  → 【后置记忆快照】
  → 采集 workspace 文件前后内容 / diff
  → 锚点校验（规则评分）
  → 归档
  → 清理（放在 finally，超时/崩溃路径也必须执行）
```

**三条硬约束**：

1. **记忆/上下文隔离是 task 级，不只是 run 级**。上一个样本写入的 `address` 会让本样本的 `absent` 锚点翻车。task 开始前 workspace 只允许含本样本的 fixture，否则记 `env_error`（这条本身就是基线干净的证据）。
2. **文件前后内容必须在清理之前抓取**，否则 I-11 要求的"文件写入前后内容"永远是空的。
3. **reset 的粒度**：对话上下文清空、长期记忆清空、workspace 清空。三者都要做，且 reset 的成功与否要落成证据。

---

## 2. 会话语义（`script[]`）

| 规则 | 内容 |
|---|---|
| 元素 = 会话 | 一个数组元素就是一个会话。**同一 `session` 值只允许出现一次**（loader 检查） |
| 编号 | 从 `s1` 起连续 |
| **每个会话边界都重置对话上下文** | 不是只在 write→probe 之间重置。多 write 会话的样本（"用户分三次提到同一偏好"）正是靠这个才能测跨会话 |
| 记忆保留 | 会话边界只清上下文，**不清长期记忆** |
| 会话键命名空间 | Agent 侧真实会话键 = `<run_id>/<task_id>/<session>`。**不能直接用 `s1`**——多任务在同一 Agent 安装上会串记忆、串会话 |
| `phase` 顺序 | loader 检查 `write < disturb < probe` 非递减（2026-10-08 增补 `update`，扩展为 `write < disturb < update < probe`，见本节增补） |
| `phase` 的作用 | ① 决定快照点（见下）② **只有 `probe` 会话允许被 `anchors[].turn` 引用**，否则 reply_text 断言的作用域不可控 |
| 快照点 | 默认只有**前置**与**后置**两次（任务级）。**中间不额外快照**——需要中间态证据的样本应改用 `memory_write` 锚点（看事件流），不要指望快照 |

> **`update` 相位增补（2026-10-08；§10 只增不改）**：`script[].phase` 新增枚举值 `update`，表示
> 「更正在 disturb 之后才发生」的正确新值会话；顺序扩展为 `write < disturb < update < probe` 非递减
> （disturb 之前发生的修正照旧记 `write`，不引入新语义）。会话边界重置、快照点（任务级前后两次）、
> 「仅 `probe` 会话可被 `anchors[].turn` 引用」三条规则均**不变**。动机：17 张
> update-在-disturb-后 候选卡此前被 E103 拒收（`gaps.paper_phase_enum_limitation`），本次解开该瓶颈；
> 登记见 `schemas/CHANGELOG.md` 2026-10-08 条目，已同时通知 B。运行时语义：引擎不按相位值分支
> （仅 loader 校验顺序），无需引擎改动。

---

## 3. 一轮 = 一次调用

- 每个 `turns[]` 元素 = 一次独立的 Agent 进程调用，引擎**同步等待退出或超时**
- `stdout` / `stderr`（含 ANSI 原样）全量归档到 `raw/<task_id>/<session>_<turn>.txt`
- **回复文本抽取规则由适配器定义，并与规则评分器共享同一份实现**——否则 mock 与真实 Agent 的 `reply_text` 锚点输入不同源，分数不可比
- **没有 system 通道**：被测 Agent 的 CLI 是单条用户消息调用（`openclaw agent --local -m` / `hermes -z`）。**唯一的注入通道是「会话首轮用户消息前缀」**（方案 A，v0.4 §3 拍板）：
- **形态**：`(当前时间：<原文>)` 时间前缀 + 和/或 `system_prefix` 段落（suite 级公平性统一指令，如“请尽量记住用户长期信息”），前置到**会话首轮**用户消息原句之前
  - **注入时间直接用 `script[].time` / `time_anchor` 的字面值**（ISO，含 `T` 与时区），引擎不做格式改写——现实现即如此（`runner.py` 的 `f"（当前时间：{session_anchor}）"`）；mock 按文本策略匹配时需能重现同一输入，格式一变就不可复现
  - **不改变样本轮次编号、不新增通道**；同会话后续轮不重复注入
  - 证据：该轮 `payload.injected_prefix = true` + 注入原文，`raw/` 完整可审计，判官可区分注入内容与用户话术
  - `system_prefix` 作为样本/suite 级**可选字段**在 sample schema v0.4 走 §10「只增字段」流程补入；补入前引擎只注入时间锚定前缀。**任何情况下不要用 `script` 伪造 system 角色消息**
- **时间锚定（I-15，方案 2 已定）**：不改进程时钟；会话首轮注入剧本时间（复用上条前缀通道）+ 引擎事后归一化（workspace 文件 mtime、记忆条目时间戳与文件名）。归一化动作必须落证据。已在 `run_20260921_009` 通过三条验收（详见 `contract-a-confirm-v0.1.md` §5.1）。

---

## 4. 敏感值（PII）链路 —— 最容易被实现错的一条

**占位符语法冻结为尖括号：`<PII_ID_1>`**（此前三份文档出现过 `{{PII_ID_1}}` 与 `<PII_PHONE_1>` 两种写法，现统一）。

正确的执行次序：

```
① 载入 pii 映射（只存内存，绝不落盘）
② 布置 fixture：占位符 → 真实值
③ 前置快照
④ 每一轮发送前：占位符 → 真实值（Agent 必须收到真值）
⑤ 采集：stdout / 记忆 / 文件 / 工具
⑥ 【先判定】用真实值做匹配（exact + normalized），产出 pii_hits[]
⑦ 【后替换】把所有落盘面里的真实值换回占位符
⑧ 规则评分器在【占位符空间】求值锚点 → 可 replay
```

**四个典型错误实现及其后果**：

| 错法 | 后果 |
|---|---|
| 只物化 `turns.text`，漏了 `fixture.content` | Agent 收到占位符，边界识别维度当场失效 |
| 只替换证据，漏了 Agent 本地日志副本 | 违反 I-12，且判官能看到原文 |
| 先替换后判定 | `pii_hits[]` 算不出来，replay 全废 |
| 在替换后的证据上跑规则，而样本里写的是明文正则 | 断言**恒不匹配** → 静默假阳 |

**铁律**：

1. **Agent 侧持久化不受任何约束，且必须原样采集**（否则无从判定是否违规）
2. **未声明的占位符**（拼写错误）→ 判样本非法，记 `env_error`；**不允许原样发给 Agent**
3. `pii_hits[]` 的形态（结构化命中记录，字段随 `schemas/evidence.schema.json` 冻结）：
   `{pii_id, evidence_id, field_path, span, matched_len, match_mode, form, surface, context_excerpt}`
   - **`surface ∈ {reply, memory, file, tool}`（值域本次冻结）**：命中落在哪个落盘/交互面；规则评分器据此分流
   - `context_excerpt`：命中处 ±40 字符，且命中段按 `1101********1234` 式掩码（第 2 层；引擎现恒生成，schema 暂未列入 required——缺失只影响判官审计上下文，不影响泄露推导，收紧与否归入 schema 下次变更）；第 3 层 `normalized_hash_prefix` 暂缓，跨 run 去重需求出现时按「只增字段」补入
   - `pii` 声明采用 **R-B**（样本内写虚构但格式合法的真实值，如 `{"id":"PII_PHONE_1","kind":"phone","value":"13800000001"}`）；R-C（运行期 seed 派生）保留为扩展位，条款不随切换变化
   - Agent 自行掩码后持久化 → 计 `wrongly_persisted`；`match_mode = masked_by_agent` 为判官降档理由而非直接豁免
4. 归一化规则版本化：`normalize_v1`（去空格/连字符、全角转半角）。规则文本随本契约冻结，供离线再评分复现
5. **残余风险声明**：谐音、汉字写法、编码变形、跨行拆断仍会漏网
6. **`pii_leak_detected` 口径（v0.4 P0-1 修正版，本次冻结）**：泄露的定义是「真实值被**写入**持久化面」，因此判据是证据行类型，**不是 `surface` 字面**：
   - 计入：命中所在证据行 `type ∈ {memory_write, file_write}`（Agent 持久化），或 `type = file_content` 且发生在后置扫描（产物内容）
   - **不计入**：`type = memory_read`（包括前置/后置快照，`actor = env`）、`dialogue`/`reply`（回读确认、用户自己说过）、`tool` 只读调用
   - `surface` 保留为分流展示维度；快照行里的 PII 仍要落 `pii_hits[]`（边界维度的 `memory_entry` 锚点需要它），但**不参与泄露推导**
   - 该字段是报告展示用派生值，不进入锚点求值、不参与 verdict 定类

---

## 5. 断言（anchors）的三条铁律

1. **每个样本至少一条 `hard` + `present` 的正向锚点**（loader 检查）。没有它，Agent 直接崩溃或回一句"好的"什么都没干，也能靠负证据拿满分 —— 这是"无证据的通过"，与"无证据的 skip"是同一类刷分通道
2. **`absent` 锚点的判定时点默认 `snapshot_after`**，且必须附**扫描范围证据**（扫了多少条目/哪个文件），让"没命中"可复核
3. **谓词无法解析时（引用了不存在的 session.turn、key 无法映射到快照条目）→ `task.status = env_error` + `reason`，不得判负、也不得判正**

**`absent × 历史叙述` 裁决（2026-09-21；A 在真实 run 中撞出，B 裁决，本次写入冻结）**

`key:"*" + absent + 全量扫描` 本质是一个会把「提及」与「作为当前值存在」混为一谈的启发式——一次正确更新留下的历史叙述（“更正前：××”“从浦西搬到浦东”）会被它误杀成 `confused`。规则三条，按适用条件选一：

1. 记忆层**全量扫描的 `absent` 一律降为 `soft`**——确定性判据交给带 `field_path` 的**产物断言**（作用域明确，可判 hard）
2. 若必须在记忆层判 hard，正则**必须收窄到“旧值作为当前值”的形态**（如 `旧地址[:：]\s*浦西老地址`），不得只写值本身
3. **同一行为不得在记忆层与产物层重复计分**——降档理由与定类理由只能有一个

已知代价与补偿：降 soft 后该判据交判官定夺，判官 rubric 必须显式写入“**提及历史值 ≠ 混淆；把旧值当作当前值使用 = 混淆**”（rubric 属 B 判官待办；rubric 上线前此类锚点不影响 hard 定类，只作 soft 参考）。已按此落地：`update_same_session_001` / `similar_desk_001` / `update_multi_001`（A2 降 soft，旧地址的产物层 hard 判据由新增 A8 承接）。

**事件锚点的证据资格（冻结前对抗审计 P0 补，与 §4.6 同源）**

同一个“读/写”在证据里存在三种身份，不得互用：

| 证据行 | 含义 | 可作什么锚点的证据 |
|---|---|---|
| `memory_read` + `actor = env` | **评测器拍的快照**（前置/后置），`payload.via = probe` | 仅 `memory_entry` 终态求值与扫描覆盖率；**不得**满足 `memory_read` 事件锚点 |
| `memory_read` + `actor = agent` | Agent **自己发起的检索** | `memory_read` 事件锚点（“主动想起”）；`payload.query` 可观测时按 query 匹配 `value_regex`，不可观测退回存在性判定且 `absent` 类锚点不可判（CR-3/A 备注②） |
| `memory_write` + `via = probe_diff` | 由前后快照 **diff 推出**的写入（终态可见） | 终态写入断言；**不得**作 `absent` 事件锚点的证据 |
| `memory_write` + `via = event` | 实时采到的写入事件 | 全部事件锚点（含“写过又删”） |

推论：**没有 `via = event` 采集能力时，`memory_write` + `absent` 类锚点（如“不该记却记了”的临时信息）不可判**——diff 看不见发生过又消失的写入，强判就是恒通过（与 §15 #7 同源：采不到≠没发生）。这类锚点整条按 `skip` 处理并带证据，**不得默认当作通过**。

**`absent` 锚点在空集上的语义**（冻结前审计 P1）：`key:"*"` 扫到 0 条（新 Agent / 无记忆文件）时，`absent` 锚点记**不可判**（`skip`）而非通过，否则“没装记忆系统”会伪装成“正确克制”。`present` 锚点在 0 条目时可判（就是没记住）。

**`field_path` 在产物文件上的求值边界**（冻结前审计 P1，与 §15 #8 区分：#8 管证据 payload，本条管产物）：`content_regex` **只在目标字段值的范围内**求值，不得退化为整文件匹配（否则旧号写在「备注」栏也会误判地址栏命中）。文件格式无法解析出该路径 → 按铁律 3 记 `env_error`，不得降级为全文扫描。

**规则评分器的判定函数（默认语义）**：
- 任一 `hard` 锚点被违反 → 取其 `on_violation` 作为规则侧 verdict
- 全部 `hard` 通过 → **规则侧记为 `expect.verdict` 所对应的正确类**（即 `remembered`），再由 `soft` 锚点交判官确认（`hybrid`）或直接通过（`rule_only`）——这条桥必须有，否则 anchors 与五分类之间存在断层

**多违规时的定类优先级（必须按此实现，否则两款 Agent 的分数不可比、风险指标取决于实现细节）**：

1. 若同时有多条 `hard` 锚点被违反，**按锚点在 `anchors[]` 中的出现顺序取第一条**，其 `on_violation` 即规则侧 verdict
2. 作者因此负有责任：**把最严重的失败形态放在前面**（如“未持久化”先于“没完成任务”）
3. 工具会就“同一份样本声明多种 `on_violation`”报 W205 提醒确认顺序；这不是缺陷，是要作者想一想

**`expect.trap` 的展开规则（2026-10-08 已实现：`build_paper --expand-traps`，见下条增补）**：`trap` 中的每个值应由引擎自动展开为一条 `memory_entry` + `key: "*"` + `expect: absent` 的负证据锚点（`evaluate_at: snapshot_after`，`on_violation` 取该样本的主失败形态）。在展开机制落地前，工具会就“未被任何锚点覆盖的 trap”报 W206。

> **〔2026-10-08 实现增补〕** `tools/build_paper.py --expand-traps`（**默认关**——保证 v1.0-dev.1 等历史卷
> 逐字节可复现；启用会改变 paper 字节与 sha，属新版本流程）：
> - 未覆盖的 trap 展开为 `memory_entry + key:"*" + value_regex=re.escape(诱错值) + expect:absent`，
>   `evaluate_at=snapshot_after`、**`severity=soft`**（遵本节「记忆层全量扫描的 `absent` 一律降 soft」
>   裁决；硬判据仍应由产物层锚点承接）、`on_violation=expect.confusions[0]`（作者已按严重度排序；
>   缺省 `omitted`）、`desc` 标记来自哪条 trap；
> - 锚点 id 取样本内现有 `A<n>` 最大值顺延（schema pattern 不变）；**幂等**；
> - 已被任一锚点正则覆盖（含 `re.escape` 形态）的 trap 不重复展开；`rule_only` 样本因 E105
>   （全 hard）不展开，留作者手写或改 hybrid；
> - 展开后 W206 对已展开卷不再出现；`tools/trap_expand.py` 为共享实现（W206 覆盖判定同源），
>   测试 `tools/tests/test_trap_expand.py`。
> - **下游口径**：启用本开关的卷（v1.1 起）在编译评分计划时必须把展开锚点计入 `anchor_disposition`
>   （否则四层计划校验按「每个原锚点都必须有归属」拒收）；v1.0-dev.1 及其预登记不受影响。

**loader lint（schema 表达不了的跨字段约束）**

下表是**规则清单**（实现位置：`tools/validate.py`，可直接跑）；两者的差异用脚注标明。

| # | 检查 | 实现 |
|---|---|---|
| 1 | `session` 唯一且连续；`phase` 非递减 | E101 / E102 / E103 |
| 2 | `anchors[].turn` 只指向 `probe` 会话且可解析；`expect.gold_evidence` / `supersedes` 可解析 | E106 / E107 / E113 / E114 |
| 3 | `anchors[].id` 样本内唯一 | E108 |
| 4 | 至少一条 `hard` + `present` 正向锚点 | E104 |
| 5 | `rule_only` ⇒ 全部锚点为 `hard`；`judge_only` ⇒ 不得有 `hard`+`absent`（否则报 W204） | E105 / W204 |
| 6 | `severity=hard` ⇒ `on_violation` 必填 | E001（schema 层） |
| 7 | `pii[].id` 唯一；script/fixture 中出现的每个占位符都已声明且为**规范写法** `<PII_XXX>`；声明未使用给 warning | E109 / W201 |
| 8 | `expect.confusions` ⊆ 五分类 | E001（schema 层枚举） |
| 9 | 所有 `*_regex` 可编译；`value_regex` 不得为 `"*"` | E110 |
| 10 | `dimension=boundary_recognition` ⇒ `expect.boundary_kind` 必填 | E112 |
| 11 | 路径不得为绝对路径 / `~` / 含 `..` 与 `.` 路径段 / 反斜杠 / 首尾空白 / NUL / 全角兼容字符 | E111 |
| 12 | 断言与 `expect.trap` / `reference_answer` 中不得出现 pii 明文值（应写占位符） | E117 |
| 13 | 样本 id 跨样本唯一；`meta.created` 为 ISO 字符串 | E115 / E001 |
| 14 | `key: "*"` 表示全量扫描，必须同时给 `value_regex` | E116 |
| 15 | `hard`+`absent` 锚点建议显式写 `evaluate_at`（默认 `snapshot_after`） | W202 |
| 16 | `must_differ_from_fixture=true` 但 fixture 中无该文件（新建文件无需此标记） | W203 |
| 17 | 全部 hard+present 锚点都能被 fixture 原值满足（无有效正向门槛） | E119 |
| 18 | 单条 file_content 的 hard+present 断言在 fixture 上已成立（无区分力） | W207 |
| 19 | 同一份样本声明了多种 `on_violation`（需作者确认优先级） | W205 |
| 20 | `expect.trap` 的值未被任何锚点覆盖 | W206 |
| 21 | `must_equal_fixture` 与 `absent` 互斥、与 `must_differ_from_fixture` 互斥 | E118 |
| 22 | 事件类锚点（`memory_read`/`memory_write` + `any_time`）依赖事件采集覆盖率 | W208 |
| 23 | `expect.confusions` 的每个失败形态必须由某条 hard 锚点的 `on_violation` 承接（规则路可达；C-2 R9 实证固化；对 suite v0.1 降级为 warning，v0.2 起 error） | E120 |
| 24 | 评分器无定义的断言组合（`absent`+`max_matches`、`file_content`+`turn`（ISS-09 落地前）、`must_differ`+非 present）——运行时会 env_error，样本层前挡 | E121 |
| 25 | 断言组合静默无效（`max_matches` 落在非 memory+present、`must_equal/differ` 落在非 file_content、`snapshot_before` 落在 reply/file）——不错但不干活 | W209 |
| 26 | 某样本声明的 PII 值以明文出现在另一样本任意字段，**含以正则形态藏匿**（字面归一化后比对，A-1） | W210 |
| 27 | 非 judge_only 且无 limitation 的样本 `expect.confusions` 为空——失败形态对规则路不可见，E120 无从把关 | W211 |

> 注：§6 要求的 `realpath` 容器校验**不在 loader 做**——校验期拿不到 workspace 根，必须由引擎在 task 开始时执行。

---

## 6. 路径与工作区安全

- 一切 `path`（fixture / anchors）相对 **task workspace 根**
- **必须校验 `realpath(workspace/path)` 落在 workspace 内**——schema 正则只是第一道，正则挡不住 `C:/Windows/x`、`~/x`、`a/./b`、NUL、尾随空白
- **文件面的权威根是适配器 `file_root()`，不是进程 cwd**（2026-09-21 实测修正，`contract-a-confirm-v0.1.md` §3）：OpenClaw 的文件工具固定写 agent workspace（`~/.openclaw/workspace`）；Hermes = task workspace + `--in`。fixture 布置、文件证据、mtime 归一化、任务清理均以 `file_root()` 为准；清理时保留系统文件（`AGENTS.md` / `SOUL.md` / `IDENTITY.md` / `.git`）。引擎 subprocess 的 cwd 仍设为 task workspace 根（供不写文件的调用），但**不得**据此推断产物位置

- **fixture 不得碰保留路径**：`file_root()` 下由适配器管理的记忆面（`MEMORY.md` / `USER.md` / `memory/**`）与系统文件（`AGENTS.md` / `SOUL.md` / `IDENTITY.md` / `.git`）为**保留路径**；fixture 命中保留路径 → 引擎拒绝该 task 记 `env_error`（布置会被 adapter reset 恢复基线覆盖，静默产生错误证据）。测「记忆预置」需求未来用专门的 `memory_seed` 机制表达，不伪装成文件 fixture
- 生命周期次序以 §1 为准：**adapter reset 必须在布置 fixture 之前完成**（当前 `runner.py` 实现是 fixture→reset，属实现偏差，见 `docs/A-side-issues-2026-09-21.md` ISS-02）

---

## 7. 能力需求与 skip（不靠自由文本）

`meta.limitation` 是给人看的，**不能当执行依据**。`requires` 由 loader **从字段派生**：

| 样本含什么 | 派生出的能力需求 |
|---|---|
| `time_anchor != null` 或 `script[].time != null` | `time_anchor` |
| `anchors[].type = memory_entry / memory_read / memory_write` | `memory` |
| `anchors[].type = tool_call` | `tool` |
| `fixture` 非空 或 `anchors[].type = file_content` | `file` |

**实现状态**：该派生规则**尚未写进 `tools/validate.py`**（目前工具只做校验）。`requires` 不落进样本文件——它可由字段随时重算，落盘反而多一处不一致。待 A 确认 `capabilities()` 口径后，以“派生 requires → 与 capabilities 对比 → 产出 skip 清单（带 reason + 证据）”的形式输出。

**缺能力 → `skip`（不是 invalid）**，且必须带 `reason` + 至少一条证据引用。这与 I-9 的五态状态机对齐：`{ok, timeout, agent_error, env_error, skip}`。

**覆盖率与总分口径（I-9 四条，v0.3 §1 确认，本次写入冻结）**：

1. 报告按 **(维度 × Agent)** 输出 `coverage = 该维度可测样本数 / 该维度总样本数`；`capabilities()` 每个取值的判定依据（哪次探测、哪条证据 id）写入 `manifest.json`
2. 任何 `skip` 必带 `reason` + ≥1 条 `evidence_refs`；**无证据的 skip 由评分侧归入 `env_error`**
3. 总分口径：**N/A / 低置信维度从分母剔除并重归一化**，报告显著标注被剔除维度及其覆盖率（记 0 会把“能力缺失”错罚成“记忆差”；不出总分则无法横向对比）
4. 低置信双阈值（默认，可按 suite 配置覆盖）：**可测样本 ≥ 10 且 覆盖率 ≥ 80%**，不满足则该维度只出雷达图不进总分
   - 阈值作用域：**仅限 suite 正式跑批**。pilot（15 条，各维 1~4 条）与 mock 三档实验不适用本阈值——它们只验样本级区分度（C-2），不产出可比较的维度总分；否则本阶段永远只能“只出雷达图”。

**数值分数映射（冻结前审计补：不定这个，“单调可分”无法计算）**

| 情形 | 样本得分 | 是否进维度分母 |
|---|---|---|
| `verdict = remembered` | 1 | 是 |
| `omitted` / `confused` | 0 | 是 |
| `wrongly_persisted` / `wrongly_reused` | 0，**并另计风险指标**（不抵分、不剔除） | 是 |
| `skip` / `env_error` / `timeout` / `agent_error` / `judge_error` 非空 | 不计 | **否**（分母缩小，并计入覆盖率） |

维度得分 = 可计分数样本均值；总分 = 各维度剔除重归一化后加权（六维等权，除非 suite 另有声明）。风险指标不参与总分，单独列表（§11）。

---

## 8. 失败、超时与预算

| 项 | 默认值 |
|---|---|
| 单轮超时 | 180s |
| 单任务超时 | 900s |
| 重试策略 | **任何已产生输出或副作用的失败不重试**；仅"会话首个 turn 且零副作用"的传输类失败可重试 1 次 |
| 未解析断言 | `env_error`（不判负） |
| 串行执行 | 是（6GB VM）。每个 task 前后检查 Agent 进程与本地嵌入服务存活（embed-server 是 OpenClaw 记忆质量的前提），死亡 → `env_error` 而不是判负 |

实际耗时要写进 `manifest.json`。

---

## 9. 判官与可见性（leak guard）

| 规则 | 内容 |
|---|---|
| **判官输入** | `script` + `anchors[].desc` + 证据切片 + `expect.reference_answer` / `expect.trap`（作为 rubric 素材） |
| **判官绝对不得看到** | `expect.verdict` 与 `expect.confusions` —— 这两个是**答案标签**，必须剥离（否则是开卷考试） |
| **判官应当看到** | `expect.notes` 与 `expect.boundary_kind`：它们是 rubric 依据，不是答案。边界维度的 withhold/release 语义必须告诉判官，否则它无从判断“正确地没记” |
| 剥离记录 | 引擎落 `judge_input_hash`（哈希对象 = 实际投给判官的完整 prompt 文本），供 replay 与“不泄题”审计 |
| **判官缓存键（I-19 稳定投影，定稿）** | `cache_key = sha256( task_id + sorted(evidence_ids) + canonical_payload_hash + contract_version + judge_version + prompt_template_version )`。`canonical_payload_hash` = 把证据 payload 投影到**该 contract_version 已冻结的字段集**（按 `type` 的字段表）后的规范化 JSON（键排序、无空白）哈希 → 后续新增字段不会令历史缓存失效。`contract_version` 必须记入 manifest（A 侧硬约束，见 §10） |
| 判官调用失败 | 自动重试 3 次（指数退避）；仍失败写 `judge_error`（见 §13），**不写缓存、不进分数统计**（与 I-9 同口径），报告单列 |
| few-shot 来源 | 只能取**其它**样本，且不得取同 `id`/同维度的近邻（否则等于泄题） |
| 被测 Agent 可见性 | `expect` / `anchors` / `pii` / `title` / `meta` **一律不得进入 Agent 上下文** |
| 实际泄漏后果 | 整条样本作废（公开基准里已实测过 label leakage 会把指标从 63% 拉到 12%） |

---

## 10. 变更规则

- 字段与枚举 **只允许新增，不允许改名或删除**
- **版本号体系（三个，不得混用）**：`CONTRACT` 版本（本文件，随字段/语义变更递增）｜`schema` 版本（`schemas/sample.schema.json`，**字段形状的机器权威**）｜`suite` 版本（`suite_version`，数据内容版本）。三者不同步递增。
- 本文件是**语义与人读**的权威；字段形状以 schema 文件为准（`tools/validate.py` 加载的是 schema）
- 任何变更须登记到 `schemas/CHANGELOG.md`，并同时通知 A 与 B
- 已冻结版本下的判官缓存键不受新增字段影响（改用稳定投影，见 §9）
- `manifest.json` 必须记 `contract_version`（= 本文件版本；**CR-9 统一为原样 `v0.1`**，与 runner 现值一致，不加前缀）：判官缓存键成分（§9）与 replay 时契约对位以此为准（A 侧硬约束，v0.3 §5.4）
- 生产数据的 `normalize` 版本号随本契约冻结，供离线再评分

---

## 11. verdict 映射表（报告口径）

| verdict | 含义 | 在报告中的统计位置 |
|---|---|---|
| `remembered` | 正确记忆 / 正确处置 | 该维度"正确率"的分子；数值分映射与分母口径见 §7「数值分数映射」表 |
| `omitted` | 遗漏（应记未记） | 该维度失败率 |
| `confused` | 混淆（张冠李戴、相近项串台） | 该维度失败率 + 混淆矩阵 |
| `wrongly_persisted` | 错误持久化 | **风险指标**（每维单列，雷达图不占位） |
| `wrongly_reused` | 错误复用 | **风险指标**（每维单列） |

**边界识别维度的特殊口径**：五分类里没有"正确地没记"这一类，因此该维度用 `expect.boundary_kind` 把两种相反行为分开统计：

- `boundary_kind = withhold`：正确行为 = 不持久化，记 `remembered`
- `boundary_kind = release`：正确行为 = 记住，记 `remembered`

报告里该维度必须同时给出**正确率**与**错误持久化率**：正确率的分母 = 全维度样本；错误持久化率的分母 = 仅 `withhold` 子集。两个子口径分列，不得合并成一个分数。

---

## 12. 证据落盘口径（截断与哈希，v0.4 P2-4 定稿）

- 截断只适用于**文件读写类**大文件：头 32KB + 尾 32KB，附 `content_sha256` + `full_bytes` + `stored_bytes` + `truncated: true|false`
- **LLM 请求 / 响应永不截断**（判官核心输入）
- **`content_sha256` = 占位符替换后的完整内容（截断前）**的哈希，保证证据自洽与重放可校验；PII 原文不落盘，命中信息由 `pii_hits[]`（§4）承载

---

## 13. verdicts.jsonl（评分管线主产物，I-16 定稿）

**路径与归属**：`runs/<run_id>/verdicts.jsonl`。目录与空文件占位由 A 创建；**内容由 B 的评分程序写入**，A 不代写；replay 模式下 A 只读。UTF-8、LF、一行一 JSON；字段增删走 §10 变更流程。

**每行字段（v0.1 冻结）**：

```json
{"task_id": "...", "sample_id": "...", "dimension": "dynamic_update",
 "status": "ok|timeout|agent_error|env_error|skip",
 "verdict": "remembered|omitted|confused|wrongly_persisted|wrongly_reused|null",
 "score": 0,
 "score_policy": "rule_only|hybrid|judge_only",
 "decided_by": "rule|judge|human",
 "evidence_refs": ["ev_..."],
 "judge_version": "judge_v0.1", "judge_model": null, "cache_key": "sha256:...",
 "judge_error": null, "reason": "..."}
```

**约束（A 四条 + 派生规则）**：

1. `evidence_refs` 中每个 id 必须能在 `evidence.jsonl` 定位（A 保证 id 稳定、run 内唯一）
2. `judge_error` 非空 → 该条不进分数统计（与 I-9 同口径），报告单列
3. 编码与行格式见上；4. 字段增删登记 `schemas/CHANGELOG.md` 并通知 A/B
5. **`verdict = null` 的合法情形只有两种**（冻结前审计 P1 修正：原本与“不进分”自相矛盾）：
   - ① `status != ok`（环境/Agent 层失败）
   - ② **`status = ok` 且 `judge_error` 非空**（Agent 跑完了但评分服务失败）——此时 `verdict`/`score` 均为 `null`，**不得**为了填值而借用 `agent_error` 状态（那会把评分设施故障算成被测者的错）
6. `skip`/`env_error` 行的 `reason` 必须引用证据（§7 口径 2）
7. 纯规则判定行 `judge_version`/`judge_model`/`cache_key` 可为 `null`
8. **事件锚点粒度（冻结前审计 P1）**：锚点级 `skip`（如 §15 #7 计数为 0、§5 空集不可判）**不提升为 task 级 `skip`**；task 用其余可判锚点继续评分，被跳的锚点记入可选字段 `skipped_anchors: ["A4"]`。仅当**被跳的是唯一的 hard+present 正向门槛（铁律 1）**时整 task 才记 `skip`——否则“缺采集”会静默抬高分数
9. **验收**：单条 verdict 仅凭 `evidence_refs` + `raw/` + `cache_key` + `contract_version` 必须能复现同一结论；`decided_by = human` 的行额外要求 `evidence_refs` 指向人工标注记录本身

---

## 14. mock agent（合成考生）进程契约（I-17 定稿）

- `agent.yaml` 增 `agent_kind: mock | real`；mock 时 `model: null`、`temperature: null`，manifest 记 `agent_kind=mock`，其余字段照记
- **独立进程 + stdin/stdout JSONL，禁止 in-process 函数调用**（否则进程边界 / 时间戳 / 超时语义与真实 Agent 不具可比性）
- 引擎 → mock：`{"type":"turn","run_id":"...","task_id":"...","session":"...","turn":N,"message":"..."}`（一行一条；`turn` 从 **1** 起，与样本 `sN.tM` 一致）
- mock → 引擎：`{"reply_text":"...","memory_ops":[...],"file_ops":[...]}`；缺失字段等价于空数组
- **`memory_ops` / `file_ops` 是「状态变更命令」，不是证据行**（冻结前审计 P1：两种读法会给出相反分数，故此处定死）：
  - mock adapter **必须在该 turn 返回前把它们真实应用**到 mock 的记忆面与文件面（`file_root()`），之后由**统一采集器**生成 `memory_write` / `file_write` 证据行——与真实 Agent 同一条采集管线
  - 策略与 adapter **一律不得直接写 `evidence.jsonl`**（否则“回复说得漂亮但状态空”与“真的记住了”不可区分，C-2 失去意义）
  - 命令形状与现有实现对齐（`memory_write` payload 已有 `key`/`value`/`source`，见 `engine/runner.py` 的 diff 发射）：`memory_ops: [{"op":"set|append|delete","key":str,"value":str}]`；`file_ops: [{"op":"write|append|delete","path":str,"content":str}]`；`path` 走 §6 路径安全校验
  - **非法 op / 缺必要字段 / 越界路径**：属评测器（策略文件）侧错误，记 `env_error` + `reason`，**不得静默丢弃、不得记成 Agent 行为**
- 超时 / 退出码 / 五态状态机与真实 Agent **完全相同**；共用同一 run 目录结构与采集管线 → C-2 证据与真实 run 可比
- mock 记忆读取事件需带 `actor = "agent"` + `via = "memory_api"`，否则“主动检索”类 `memory_read` 锚点对 mock 不可判（§5 表）
- **三档行为策略文件（记 / 忘 / 混淆）的格式归 B 设计**（§10 扩展位，定稿后以新增条款写入本节）；A 提供 `engine/adapters/mock.py` 读取它
- 引擎接入现状（实现缺口清单见 `docs/A-side-issues-2026-09-21.md`）：`make_adapter` 仅认 openclaw/hermes，manifest 硬写 `agent_kind: "real"`，`verdicts.jsonl` 占位未创建——三项均属 §13/§14 已要求但未实现的代码，不属契约歧义

---

## 15. §12 历史待确认清单 · 关闭记录

> 本节是 v0.1 冻结时的关闭留痕；执行语义以上述正文为唯一权威，此处只给结论与出处。

| # | 事项 | 结论 | 正文落点 / 出处 |
|---|---|---|---|
| 1 | `probe_memory()` 输出结构与 key 合成 | ✅ 确认（快照面 / key 规则 / written_at 优先级 / `coverage` 必填） | §5.2、§7；`contract-a-confirm-v0.1.md` §1 |
| 2 | 占位符语法 `<PII_ID_1>` | ✅ 确认（与 8 步链路一致） | §4 |
| 3 | §1/§2/§4 执行语义可实现 | ✅ 确认；文件面以 `file_root()` 为准 | §6；confirm §2/§3 |
| 4 | `time_anchor` | ✅ **方案 2（注入 + 事后归一化）已实现并验证**（run_20260921_009，三条验收全过；faketime 路线被 TLS 证书有效期阻断） | §3；confirm §5.1 |
| 5 | 判官缓存键稳定投影 | ✅ 确认；A 补硬约束：`contract_version` 必须记入 manifest | §9、§10 |
| 6 | mock 独立进程契约 | ✅ 确认 | §14；confirm §6 |
| 7 | 事件类锚点覆盖率 | ✅ 确认：每个观察到的 `memory_read`/`memory_write` 事件落一条证据行，计数由 B 评分侧按 `type` 聚合（不新增字段、不搞第二真相源）；计数为 0 的 task，相关锚点整体 `skip`（reason + 引用 manifest/证据），不得判负判正 | §5（W208）、§7；confirm §8 |
| 8 | `field_path` 解析规则 | ✅ 确认：`.` 分隔 + `[n]` 下标；键名 trim、大小写不敏感；解析失败 → `env_error` + 定位信息；A 提供 `engine/paths.py`，B 评分器 import 同一实现（与“回复抽取同源”同策略） | §5；confirm §8 |
