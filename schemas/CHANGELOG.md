# schemas/CHANGELOG.md — 数据契约变更记录

> 规则：字段与枚举**只允许新增，不允许改名或删除**。任何变更在此登记一行，并同时通知 A 与 B。

---

## 2026-09-30：S5家族隔离与冻结候选清单草案

- 新增 `okb-semantic-split-manifest-v1-draft` / `okb-semantic-split-review-v1-draft`，独立外部清单绑定显式提供的案例源字节与逐条请求；原33例及旧schema不改。
- 家族/split与等价组一致性、跨源重复请求、全量成员覆盖严格检查；120案例/30家族/三态各30/12组三表达等门槛固定。缺字段准备、数量不足和暴露声明保留为未满足项，开发数据不得转验证。
- `review-semantic-split`仅离线检查与新目录快照，不认证独立性/真实标签/封存时间，不授权调用或发布。声明内容和协议指纹还须外部核验；未发送队友消息或替代会签。

## 2026-09-29：S5开发语义双人标注草案

- 新增okb-semantic-human-work/labels/package/pair-v1-draft，由semantic_annotation严格校验；两个随机视图、私有映射、原始字节绑定、明确真人/信息暴露声明。
- 逐行status区分labeled与needs_clarification，后者result=null；三态支持/矛盾须真实引用。分歧输出保留完整分母、初始标签与暴露记录，仲裁参考固定null，不自动产生金标准。
- 新入口annotate-semantic仅支持开发用途，不替换旧五分类标注，不证明独立真人/作者/封存时间；发放仍待审阅。24项离线测试，未发送队友消息或代替接口会签。

## 2026-09-29：S3离线预算审阅草案

- 新增 `okb-judge-cost-review-v1-draft`、`okb-judge-price-quote-v1-draft`、`okb-judge-token-counts-v1-draft`，由cost_review严格校验；模型/端点、请求集合、逐条发送报文指纹绑定调用方提供的报价及token记录。
- 缺资料时金额null，字节数不换算token；情景不提供费用硬上限或执行授权。历史导入只接受重建通过的v2/scoped prepare，来源拒判与记忆阻断项单列。
- 新入口review-judge-cost只写全新审阅目录和完成指纹；旧评分、账本、冻结证据schema不变。22项离线测试见第八批说明；未发送队友消息或代替接口会签。

## 2026-09-29：S1/S7配置声明与尝试留存草案

- 显式retained-run的manifest新增attempt_retention（okb-retained-run-v1-draft）、runtime_identity_unchanged、usage_complete（中断false/正常未知null）；agents新增runtime_identity（okb-runtime-identity-v1-draft）。model/temperature仅从指定本地配置位置读取，不当作实际返回。
- 中断task新增interrupted、usage_complete=false，仍使用env_error拒判；保留已落盘证据及已知usage。默认旧runner、旧schema不变，跨run配置校验拒绝采集错误或漂移。
- 22项离线测试与限制见第七批文档；真实部署未验收，未发送队友通知或替代接口会签。

## 2026-09-29：S7跨运行注册与诊断草案

- 新增okb-multirun-plan-v1-draft与okb-multirun-imports-v1-draft，由 `tools/scoring/multirun.py` 严格校验：冻结协议/配置、批次、全局repeat、attempt及retry_of；当前仅支持first_attempt_only和scoped-v1/v2。
- 新输出okb-multirun-report-v1-draft保留完整计划分母、缺批占位及全部尝试历史；新入口report-multirun强制逐项追溯，拒绝未知配置、重复导入及运行内回滚重试。旧输入schema、历史manifest与默认评分不变。
- 配置版本/范围取自已有开放config字段，但真实生产采集尚未补齐；目前验收来自合成adapter。哈希不证明注册时序或有效运行参数，不生成正式排名。39项测试与操作合同见第六批文档；未发送队友通知或代替会签。

## 2026-09-29：S2/S7可选报告追溯草案

- `okb-diagnostic-report-v2-draft`新增trace_verification；未启用时status=not_requested。显式report-vnext --verify-traces时使用 `okb-report-trace-v1-draft`，任务增加trace、逐项状态、请求指纹、引用、投影正文和raw定位。
- 派生文件状态标记derived_file_state，来源无效保留source_invalid；重建仅证明与当前投影一致，不是语义正确性或模型/代码身份认证。旧评分结果、冻结证据schema及默认legacy未修改。
- 20项离线测试和合同边界见分批说明第五批；多run仍未实现。未发送队友通知，待接口会签。

## 2026-09-29：S2/S7诊断报告草案

- 新输出 `okb-diagnostic-report-v2-draft`，由 `tools/scoring/report_v2.py` 读取完成且输入未变化的v2评分包生成，不改变冻结evidence或task-outcome字段。
- 增加维度/Agent的任务数、完整可判样本数、覆盖、共同配对子集及排除清单；未知为null，正式总分和排名固定null。报告不是旧五分类报告迁移完成的标志。
- dispatcher新增score-vnext/report-vnext，旧入口不变。详见分批实现文档第四批及27项报告测试；跨run与公开包合同尚未完成，未代替真人会签。

## 2026-09-29：S1/S2完整文本与离线控制轨迹（草案，冻结schema不变）

- `MemorySnapshot.full_capture` 为内部可选字段，不进入旧 `as_dict()`；runner脱敏后通过既有system/message开放payload落 `memory_documents`，回执新增 `full_capture_ref`。
- 完整文本表示版本 `okb-memory-file-capture-v1-draft`：原字节与脱敏后UTF-8各自记录长度/哈希；包含枚举状态、文件路径、正文和源mtime。`tools/scoring/memory_provenance.py` 实施精确字段及内容校验。
- 独立控制轨迹版本 `okb-memory-control-trace-v1-draft`：冻结binding、marker、write_method和baseline/observed/cleaned三阶段；只验证提供的轨迹，永不自动授予完整记忆门资格。
- `scoped-v1`新增 `full_capture_status`，检查完整表示与原始文件、快照及回执的关联；全部记忆依赖仍未决。新旧prepare六份基准逐字节不变。
- 29项新测试及局限见 `docs/scoring-vnext-batch1.md` 第三批；真实部署、控制执行和接口会签仍待完成，未发送通知。

## 2026-09-29：S1/S2第二批接线（草案，既有schema不变）

- OpenClaw/Hermes本地probe产生可选scope元数据；runner使用既有system/message开放payload落 `observation_receipt`，before/after分别落raw；状态固定partial，不宣称控制或实例已验收。
- 显式 `--outcome-version v2 --evidence-policy scoped-v1` 接入按项证据门；不认可自报完整回执、不修改旧blind projection。
- `docs/scoring-vnext-batch1.md`末节记录行为、历史raw不一致阻断和26项新增离线测试；生产会签、完整控制证明及真实部署仍待完成。

## 2026-09-29：S1/S2独立旁路契约（不改冻结evidence schema）

- `observation-receipt.v1.schema.json`：S1观测状态、实例/范围/时间、控制证据草案；尚未接入生产适配器，不允许据此放宽旧证据门。
- `task-outcome.v2.schema.json`：S2双轴结果字段与一致性约束；独立 `okb-four-layer-v2` 输出，默认v1不变，不可直接混入旧报告。
- 接口与迁移说明：`docs/scoring-vnext-batch1.md`。A侧接口确认与生产验收待完成；本条不声称已通知或会签。

---

## evidence.schema.json

### v0.1-r1（2026-09-22，A）— ISS-03 / ISS-07 配套字段（只增）

| # | 变更 | 为什么 |
|---|---|---|
| 1 | `memory_read` payload 新增可选 `via: "probe" \| "memory_api"` | CONTRACT §5 事件锚点资格表（CR-3）：快照行必须能与 Agent 主动检索事件区分；快照恒为 `probe`，事件为 `memory_api` |
| 2 | `memory_read` payload 新增可选 `error: string \| null` | ISS-07：快照失败时 `entries: []` + `error` 需要落点，`additionalProperties:false` 原本会拒 |

配套：`engine/runner.py` 快照行恒发 `via=probe`；失败路径发 `error` 后记 `env_error`；`manifest.agents[].event_coverage` 声明事件采集覆盖率（键名 `memory_write_via_memory_api` / `memory_read_via_agent`，已建议 B 将 `tools/simulate.py` 的旧键名 `memory_write_via_event` 随 CR-3 统一）。

---

## sample.schema.json

### v0.4（2026-10-08）— `phase` 枚举新增 `update`（A 决定；只增不改）

| # | 变更 | 为什么 |
|---|---|---|
| 1 | `script[].phase` 枚举新增 `update`；顺序扩展为 `write < disturb < update < probe` 非递减 | 17 张「update 发生在 disturb 之后」候选卡此前只能被拒（映射 write 触发 E103、映射 disturb 语义相反），`dynamic_update` 维只剩 5/15。新增相位表达该场景；disturb 之前的修正照旧记 `write`，不引入第二套语义 |

配套 loader 检查：`E103` 顺序表与文案同步（`tools/validate.py`）；负向样本 `neg_e103_update_before_disturb`、正向 `pos_control_update_phase`（`tools/tests/negative_cases.yaml`）。旧枚举值、文档其余规则、CONTRACT 会话语义全部不变；引擎不按相位值分支，无需改动。17 张卡以 **v1.1** 新版本重编译进入（不动 v1.0-dev.1 的 sha/预登记）。

### v0.3（2026-09-21）— 写 pilot 样本时发现的两个缺口

| # | 变更 | 为什么 |
|---|---|---|
| 1 | 新增 `anchors[].must_equal_fixture` | 写杀手样本 #10（按模板写周报）时发现：「参照物不得被改动」这个断言写不出来——当时只有 `must_differ_from_fixture`（要求必须有改动），没有反向字段 |
| 2 | `anchors[].id` 的 pattern 由 `^A[0-9]+$` 放宽为 `^A[0-9]+[a-z]?$` | 把一条断言拆成多条地址时（A2 → A2a/A2b）保留原有编号，而不是被迫重排全样本的锚点号 |

配套 loader 检查：`E118`（must_equal 与 absent/must_differ 互斥）、`W207`（单条断言在 fixture 上已成立）、`E119`/`W205`/`W206`（见下）。

### v0.2（2026-09-21）— 经三方评审后重写

**修复的缺陷（已用 `jsonschema.Draft7Validator` 实测复现）**

| # | 问题 | 修法 |
|---|---|---|
| 1 | `anchors` 的 `allOf` 分支属性被 `additionalProperties:false` 拒绝 → **schema 连自己的示例都校验不过（8 条错误）**。draft-07 不会把 `allOf` 分支的 `properties` 合并到父级 | 类型专属字段全部提升到 `anchors.items.properties`，`allOf` 只保留 `required` 约束 |
| 2 | 示例 `boundary_pii_001` 的 A2 缺 `expect` 字段 | 补齐 |
| 3 | `meta.created` 在 YAML 里未加引号 → 解析成 `date` 对象，`json.dumps` 直接抛错 | 改为字符串 + ISO 正则，示例中加引号 |

**新增字段**

| 字段 | 为什么 |
|---|---|
| `anchors[].on_violation` | 没有它，五分类 verdict 无法从规则路导出，`rule_only` 退化成二值，`confused` / `wrongly_reused` 只能靠判官猜，每维风险指标统计不出来 |
| `anchors[].evaluate_at` | 判定时点从描述文本变成可消费字段，避免 `absent` 锚点选错快照恒通过 |
| `anchors[].type` += `memory_read` / `memory_write` | 「记忆读写记录」是赛题四类证据之一，此前不是可断言类型 → 「主动检索」与「写过又删」都判不了 |
| `anchors[].key_regex` / `match_scope` / `max_matches` | 真实记忆后端没有稳定键名（键由适配器合成）；`match_scope` 默认 `both` 防止分数方向由键名巧合决定；`max_matches` 用来判定"同一 key 下并存冲突条目"这种 confused 形态 |
| `anchors[].field_path` | 没有字段级定位，相近区分维度的核心失败形态 `confused`（号码填错栏）判不出来 |
| `anchors[].must_differ_from_fixture` | 否则 fixture 里本就有同名文件会白送 `present` 锚点 |
| `anchors[].turn` 扩展到 `tool_call` / `file_content` + 支持 `"*"` | 限定"动作发生在探测轮"；全集量词此前无法表达 |
| `anchors[].action`（替代自由文本 `tool`） | 统一用 `action_map` 的抽象动作，避免样本绑死某款 Agent（通用性丢分） |
| `expect.reference_answer` | 给判官一把可比的尺子（此前只有散文 notes） |
| `expect.gold_evidence` | 打开「记忆调用」维度的量化口径（证据命中率、写→探距离） |
| `expect.trap` | 诱错值机器可读 → 规则评分器可自动展开负证据锚点，混淆矩阵不必手填 |
| `expect.supersedes` | 规则评分器据此自动补出成对断言，避免作者漏写负证据 |
| `expect.boundary_kind` | 五分类里没有"正确地没记"，用它把边界维度的两种相反行为分开统计 |
| `script[].time` | 样本级 `time_anchor` 无法表达"s1 是 5 月、s3 是 7 月" |

**删除字段**

| 字段 | 为什么删 |
|---|---|
| `injection`（含 `mode` / `writes[]`） | 当前只支持 dialogue 注入；`memory_api` 的写接口签名从未定义、`file_drop` 无法表达"投哪些文件" → 保留无用字段只会诱导实现者私自补假设。待 A 定义 `write_memory()` 后按"只允许新增"规则加回 |
| `script[].turns[].role` | 被测 Agent 的 CLI 只有单条用户消息入口，没有 system 通道 |
| `fixture[].encoding` | 单值枚举恒真，无消费者 |

**语义冻结（详见 `docs/CONTRACT.md`）**

- 占位符语法统一为 **`<PII_ID_1>`**（此前 `{{PII_ID_1}}` 与 `<PII_PHONE_1>` 两种写法并存）
- 一个数组元素 = 一个会话；每个会话边界都重置对话上下文
- 只有 `probe` 会话允许被 `anchors[].turn` 引用
- 每个样本必须至少有一条 `hard` + `present` 的正向锚点
- `expect.*` 运行期绝对剥离，不得进入判官 prompt 或 Agent 上下文

---

### v0.1（2026-09-21）— 初版

字段：`id / dimension / difficulty / title / tags / injection / fixture / pii / time_anchor / script / anchors / expect / score_policy / meta`

## 2026-09-21 · 校验器 lint 固化（P1-2；schema 字段不变，CONTRACT §5 表追加 23~26 行）

- **E120** verdict 规则路可达性（C-2 R9 实证固化；v0.1 对照组降级 warning，已验证对 v0.1 报出 4 格与实验记录吻合）
- **E121** 评分器未定义组合前挡（absent+max_matches / file+turn / must_differ+非present）
- **W209** 静默无效组合提醒 · **W210** 跨样本 PII 明文冲突（D1 根因巡查）
- 负向集 38→44 条（每新码 ≥1 条 error 级案例 + W 码案例，全过）；发现并登记
  v0.1 文件 3 条样本漏写 meta.suite_version 的历史遗漏（用文件级兜底，锁定的 v0.1 不改）

## 2026-09-22 · CONTRACT v0.1 冻结（会签闭环）

- A 回签 `docs/contract-countersign-v0.1.md`：CR-1~9 · NP-1~4 · Q1~14 全 ✅（3 备注由 B 吸收：
  simulate 键名统一 / query 语义入 §5 表 / A-fixtests 补发）
- CONTRACT 头部改判「v0.1 已冻结」；§10 contract_version 统一为 `v0.1`（CR-9）；§5 资格表
  memory_read 行补 query 语义（CR-3）；修复头部块历史重复行（第 2 轮编辑遗留）
- B 侧吸收：评分器 read 锚点升级为 query 可观测匹配（U16 四断言）；simulate 不再自造
  event_coverage 键；变异套件对 A 新引擎全过（8/8），c2 集成链 exit 0

## 2026-09-28 · W212 工具告警落地（F-9；不变更 schema 或评分语义）

- W212：`hard + reply_text + present` 正则中的可识别字面词元与 `expect.reference_answer` 重叠时警告，提示参考措辞依赖风险；不自动重判、不自动降 soft。
- 实现范围：连续中文片段长度至少2，ASCII词长度至少2且按词边界匹配；跳过字符类、转义、量词和分组语法。属于启发式筛查，不声称完整解析任意正则；单字、编码转义、拆分字符类、语义改写仍需人工复核。
- 正常校验仅 warning；显式 `--strict` 沿用既有“任何warning均失败”的语义。历史v0.2题面/指纹不改；新题的warning需逐项处置或登记limitation，不能将“0 error”冒称风险已清零。
- 配套：`negative_cases.yaml` 新增2条warning样本，`test_w212.py` 含11项正反控制与真实F-9锚点复现。完整离线验收见 `docs/stage1-offline-readiness-2026-09-28.md`。
- CONTRACT冻结正文未改，编号说明先登记于此；A/B通知文本追加于A需求单§F，尚待提交推送与会签。


### 2026-10-08 · dynamic_update 三相语义（F3 处置：本次排除 + 缺口公示）
- 决定：17 张「update 发生在 disturb 之后」的候选卡**不进入本次冻结提案**，`phase` 三相枚举保持不变。
- 理由：把 update 映射成 write 会触发 E103（错误语义）；映射成 disturb 语义正好相反。映射规则属
  CONTRACT 层，B 不擅自改写判据——改判据会让已签发的人审答案作废、测量前后不可比。
- 后果公示：交付卷 `dynamic_update` 5/15（缺 10），量化见 `notes/freeze-status-v1.0.json`，
  登记见 `notes/p5-preregistration-v1.0.md` §7 R-1；数据卡与报告口径必须带这条。
- 待 A 决定：`phase` 枚举映射方案；若扩枚举，这 17 张作为 **v1.1** 进入（版本只增不改）。

### 2026-10-08 · A 决定：扩 `phase` 枚举（F3 待决项关闭）
- 回应上一条「待 A 决定：`phase` 枚举映射方案」：A 选择**新增 `update` 相位**（顺序
  `write < disturb < update < probe`），而非改写旧相位语义——旧值、旧规则、旧卷全部不受影响（只增不改）。
- 17 张 update-在-disturb-后 候选卡由 B 以 **v1.1** 重编译进入（新 sha）；B 的编译器
  `--map-update-phase` 开关届时启用；v1.0-dev.1 及其预登记/已跑批次的可比性不受影响。
- 登记：见本文上节 sample.schema **v0.4**；配套负向/正向测试见 `tools/tests/negative_cases.yaml`；
  已同时通知 B（本条为通知留痕）。

### 2026-10-08 · 混淆判据派生与红线1 三读法（R-2 处置：补实质 + 并报，阈值不动）
- **本次改动不动 schema/枚举/字段**：`FAILURES` 四值、`sample.schema.json`、CONTRACT 正文均未改；
  新增的只是四层计划里的检查项（`okb-four-layer-v1` 既有形状，`validate_plan` 原样通过）。
  （同批合并进来的 A 侧 v0.4 扩 `phase` 枚举是另一件事，见上两节；两者互不依赖。）
- 新增编译器开关 `tools/freeze_candidates.py --error-control-checks`（**默认关** ⇒ dev.1 四份产物
  逐字节可复现，`test_paper_r1_reproducible.py` 继续钉住 `f30541ae…`/`5bed443d…`/`0da83755…`）。
  打开后为每张合格候选卡在计划里派生 1 条 `on_failure=confused` 的语义检查，实测 +44 条
  （295 → 339 项强制检查）。
- 材料只用卡片自带的 `authored_error_control.example`（出题人登记的错误对照）与
  `allowed_equivalents`（原样嵌进判据文本），**一个新关键词都没造**；资格三条全机械
  （`tools/freeze_gates.py:ERROR_CONTROL_RULES`），逐卡台账落
  `notes/paper-r1-dev2-coverage-matrix.json` 的 `error_control_derivation.per_card`。
- **卷一个字节没改**：`samples/paper-r1.yaml` / `build/paper-r1.jsonl` 的 sha 开关前后相同
  ⇒ dev.2 = dev.1 的卷 + 新计划（`samples/four-layer-r1-dev2.json`，`a96e5bbf…`）。
  不另出 `paper-r1-dev2.yaml`（与 dev.1 逐字节相同，复制只会在仓里留第二个真相源）。
  试卷 `expect.confusions` 也**没有**跟着改：`validate.py` 的 E120「声明可达性」要求每个声明形态
  都有 hard 锚点承接，结构锚点承接不了 `confused`，一声明就是 error。
- `tools/freeze_status.py` 红线1 改为**三读法并报**（A 锚点字面 / B 计划 confused 单类 /
  C 计划内容错三类），三个数全进凭据，验收只认 C；A 读法在 dev.2 上仍报 2/6 未过，不用 C 掩盖。
  新增 `--plan`（必须与卷同源，否则退出 2）与 `provenance`（卡片编译题数现场量）。
- `tools/freeze_broadcast.py` 新增 `--paper-yaml/--paper-jsonl/--plan/--provenance`；数据卡正文
  改为**全部由凭据数字现场拼**（上一版把"memory_recall 只有 1 条、similar_discrimination 有 26 条"
  写死在正文里，换读法之后就成了没人对的旧数）。
- 登记：`notes/p5-preregistration-v1.0.md` §8 R-2（append-only，§1–§7 一字未改，阈值「每维 ≥8」未动）；
  同节 §8.1 **撤回**了「8 条 W206 → 6/6 维达标」那条承诺（现场量只够 3/6，
  `memory_recall`/`long_term_retention` 一条诱错值材料都没有）。
- 守卫：`tools/tests/test_error_control_derivation.py`（25 例，含 7 处变异验证——顺序错、类别造假、
  资格放水、读法少算、同源闸拆掉、开关默认打开，全部被抓到）；审计器新增
  「红线1 三读法都在凭据里且与过/未过一致」核对，并把 dev.2 计划纳入指纹守卫的已知产物集合。
- 仍未取得：44 条派生属**作者代理判断**，未经真人审阅（卡片 `human_truth_label` 全 null），
  归入 F1 人审复核、可否决任意一条；红线2/3/4、F1/F2 在 dev.2 上**仍未过**（通过 1 / 未过 5）。
### 2026-10-08 · anchorcal 落地（红线4）：把"锚点在真实证据上能不能判"变成可复算凭据
- 新增 `tools/anchorcal.py`（`okb anchorcal`）：把交付卷 **257 条锚点**逐条放到真实批
  `runs/run_v02_first`（64 任务 / 200 条真实 Agent 回复 / 832 条记忆条目 / 50 份产物正文）上校准，
  凭据 `notes/anchorcal-paper-r1.json`（`--check` 复现）。**schema/枚举/卷/计划一个没改**，只读。
- 核心口径是把两件正交的事拆开，否则工具会**冤枉锚点**（第一版实测冤枉了 3 条，已钉成回归用例）：
  ① **谓词可实现性**：正则在**全量真实语料**上能否命中（红线4 字面要的"零正则永不命中"）；
  ② **证据到位性**：这道题在真实批里有没有任务、产物路径有没有真被写出来、记忆探针是否被证实。
- 证据分层（file_content）：真实 `file_write` 正文 > 题面 `fixture` 正文 > 工作区清单（listing，
  只有 sha/size ⇒ 记"内容不可得"）。第一版只看 `file_write`，把 fixture 路径 `周报模板.md`
  冤枉成"从未落盘"——fixture 是初始文件，永远不会出现在写入事件里。
- 实测读数（**全部可复算，含对我不利的**）：死锚嫌疑 **0**；恒真无区分力 **129/257**
  （`.*` / `.+` 结构锚点，命中率 ≥0.99 判定，阈值写进凭据）；证据不在但谓词可实现 **22**；
  **产物路径从未落盘 8**（真实批里 Agent 落盘叫 `周报-2026-09-25.md` 而锚点钉死 `周报.md`；
  `输出-临时/报告.md` vs 锚点钉 `输出/报告.md`）；未验证负判据 **3**；不可判 **33**；
  事件类锚点在任何 probe-only 采集下都不可校准 **6**（CONTRACT §5）。
- 红线4 判据升级：原先"`notes/*anchor*cal*` 文件存在即过"——扔个空文件就能关掉一条红线。
  现在必须①凭据量的就是这份卷（`paper_sha256` 对得上）②死锚嫌疑 0 ③每条锚点都可判。
  **结论仍是未过**（真实批只覆盖 20/100 题 ⇒ 33 条不可判），但理由从"无凭据"变成实测缺口。
- 守卫：`tools/tests/test_anchorcal.py`（23 例）—— 合成 run 上钉住每种状态的判定边界，
  真凭据上钉住三条冤案不得复发（`retain_allergy_001:A1` 不得判死锚、未跑过的题不得被指控、
  fixture 路径不得判"从未落盘"），并**反向钉住真发现不许被少报**（`path_never_produced` ≥5 条、
  必须点名 `reuse_report_001:A1` 与 `boundary_temp_001:A1`）。
- 自曝：写这套守卫时我自己先制造了一片假红 —— 一个用例把共享夹具的记忆条目清空后没还原，
  污染同类后续 8 个用例（语料池 6→4 条 ⇒ 全体误判 `undecidable_small_pool`）。已改成显式存/还原
  并断言还原成功；教训写进用例 docstring。
