# 人工独立证据包：工程协议草稿 v1

状态：**工程准备，review_required，未批准正式发放**。本协议不修改冻结 CONTRACT、判官提示词或预注册；正式指南和统计方案需另行会签。

## 两种视图不能混称

| 入口 | 用途 | 可见内容 | 标签兼容 |
|---|---|---|---|
| annotate.py make-worklist | development-calibration，开发校准 | 原判官同源摘要，含 anchors_desc、soft 状态、reference 等 | 保留原 R01 格式和旧工具兼容 |
| annotate.py make-blind-worklist | independent-human-draft，独立人工证据草稿 | 用户脚本/时间、fixture、完整对话与记忆快照/产物、工具观测 | 新随机 package_id/row_key，禁止拼接旧标签 |

新入口**不读取 verdicts.jsonl，不调用锚点评估或判官**，不按已有裁决筛选或分层；只处理操作者预先明确的任务清单。清单应由将来会签的抽样方案产生，本工具不替代抽样设计。重复任务/样本依赖需统计方案处理，随机行号不等于样本独立。

## 使用（均为离线操作）

私有 selection.json 示例，run_dir 相对 selection 文件位置解析：

```json
{"rows":[{"run_dir":"../runs/example","sample_id":"sample_001","task_id":"agent-sample_001"}]}
```

```bash
python tools/annotate.py make-blind-worklist --selection private/selection.json --paper build/paper-v0.2.jsonl --public-dir build/human-public-001 --private-dir build/human-private-001
python tools/annotate.py validate-blind --worklist build/human-public-001/worklist.json --labels labels-person1.jsonl
```

public-dir/private-dir 必须都是新目录，且相互不嵌套。公开目录只含 worklist.json；私有目录包含 keymap.json 与最后写出的 complete.json。失败批次不复用；I/O 中断可能留下不完整目录，必须核验 complete 和指纹后才能进入发放审核。该隔离是文件布局，不是操作系统权限控制；须由操作者设置访问权限。

标签 JSONL 每行：

```json
{"package_id":"包内值","worklist_sha256":"worklist.json原始字节SHA256","row_key":"本包随机行号","label":"remembered","note":"依据E3、E8；记录具体理由"}
```

五分类外允许 `unassessable` 记录证据不足或语义歧义，它不是第六种能力分值；未来 κ 分母、拒判率、仲裁方式必须先登记，不可为了提升一致性直接删掉拒判行。
所有行必须唯一、齐全且绑定同一包和精确文件字节；换包/旧 R01 标签不接受。旧 validate 入口拒绝独立包。校验通过只证明标签文件结构和绑定关系，不证明标注正确、人类身份或独立性。

## 可见字段与泄漏边界

- 不输出任务/样本/Agent名称、模型、run/证据原ID、raw路径、规则/锚点描述与状态、参考答案、expect.notes、标签、私有输入指纹、mock policy。
- 任务语义白名单：dimension、time_anchor、script.session/time/turn.text、fixture.path/content；边界题保留 boundary_kind（任务方向，而非评分结果）。脚本 phase/note、title 不输出。
- 证据用每行 E1… 编号；保留会话轮次、角色、正文、注入时间前缀、记忆 key/value/时间、文件相对路径/前后内容、工具 action/args/result、时间归一化的实际观测信息。
- 快照从 raw 全量加载，不截到前10条；对话/文件正文不截240字符。所有原证据行 raw_ref 都要求存在且位于本run的raw目录，私有审计保留SHA。
- 以 evidence payload 为正文来源（memory快照和listing从raw读取）；**不保证所有payload与raw逐字段一致**。历史raw重复引用/字段不同的问题需单独对账，本工具不重写它们。
- 白名单只消除结构化提示；自由文本/工具参数/文件名仍可能带身份或敏感信息，任务内容也可能被熟悉题库的人识别。不能宣称完全匿名、自动脱敏或零泄漏。先人工审读、PII检查、身份线索审查，必要时登记转换并升版，不能静默删改证据。

## 证据门（偏保守，不是对所有未来adapter的通用规范）

1. manifest唯一status=ok、任务证据存在、证据ID唯一且序号严格递增。
2. 原脚本与实际用户文本一致（允许已记录时间前缀）；每轮完整用户/Agent回复。重试多回复、部分转录目前拒绝，不静默选一条。
3. 必须有唯一before/after记忆快照，包围对话；有采集失败或未证实真实记忆探针则拒绝。文件扫描数>=1，条目计数与实际raw相符。
4. 文件正文不得截断，写后内容与字节数/SHA匹配；末态listing每个文件必须能由fixture或写事件重建，有size/SHA则必须一致。换行差异、脱敏前后hash或无法重建也拒绝，不能称考生失败。
5. 有error、failed health/reset、失败工具结果则拒绝。工具/检索事件缺失不能自动判omitted；人工可选择unassessable。
6. 全清单预检完成后才建输出目录；任一任务不合格则整包拒绝，不静默丢行。

保守门会减少旧数据可用量，必须报告拒绝数量/原因，不能把通过检查的便利样本当作总体随机样本。

## 正式发放前置

- 独立代码/方案复核；当前代理上限阻塞，无独立GO。
- 两名真实标注员确定并隔离标签；不得接触keymap、模型判决或对方标签。
- 重新登记独立抽样框（目标>=50行仍需考虑题目/重复轮聚类、类别覆盖及训练/验证分离）。当前工程示例不是正式验证集。
- 人工指南、题义歧义、unassessable和仲裁/κ口径会签；本包guide只是一般标签说明，不宣称复现所有冻结规则细则。
- 新人工视图与旧判官视图不同；不能沿用旧κ作“同视图一致性”。若修改判官视图，必须升模板版本并重建基线，本轮未修改。
- 从私有complete/keymap核验包字节；公开目录内容人工审阅后才发放。不要发送本地混合mock/真实工程示例给正式标注员。
