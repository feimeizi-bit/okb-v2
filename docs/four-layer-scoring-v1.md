# 四层评分旁路：实现、复现与接手状态

> 后续推进入口：[冲冠优化主方案 v1.0](championship-optimization-master-v1.md)。本文记录当前v1实现与接手测试；主方案中的vNext改动和验收目标尚未实现，不应混淆。

核验日期：2026-09-29。当前是第一版旁路工程实现，不是已验证的语义评分器，也不是正式能力成绩。

## 1. 接手基线

- 主仓库：`D:/a_shanghai/okb-repo`；核验时 HEAD 为 `9ea567495f6adae282a69e3795ffa1d57bc0773f`。
- 接手时以下 7 个文件均为 Git 未跟踪文件；现有提交不包含本轮四层实现。运行目录内的历史输出不等于已经提交的功能。
- 当前方向以用户本轮要求及 `benchmark-spec-v1.1.md` 为准：先升级评分可靠性、完成小批闭环，再扩到 100 题。旧交接中的立即全量跑批建议不构成本轮授权。
- 本轮仅进行本地读取、离线测试、prepare 重跑及文档整理；未调用真实判官、未运行真实 Agent、未重置 VM、未提交或推送。

## 2. 代码定位

| 文件 | 实际职责 |
|---|---|
| `tools/score_four_layer.py` | 独立旁路入口；prepare / replay / live；新输出目录；输入锁和完成标记；保留旧规则结果作对照 |
| `tools/scoring/four_layer.py` | 计划验证、四层路由、客观检查、证据范围选择、未决项与复核队列 |
| `tools/scoring/fact_judge.py` | 三态语义协议、JSON 与引用校验、精确请求/模型回放、显式在线传输与调用次数限制 |
| `samples/four-layer-pilot.v1.json` | 两题迁移方案；逐项说明旧锚点如何映射，以及哪些口径发生变化 |
| `tools/semantic_robustness.py` | 反例请求生成、执行/回放、覆盖率及鲁棒性指标；预设标签不进入请求 |
| `samples/semantic-robustness.v1.json` | 33 个作者设计案例：13 supported、15 contradicted、5 insufficient |
| `tools/tests/test_four_layer.py` | 43 项程序测试；包含伪判官与离线端到端回放，不能当作模型语义准确性测量 |

第一层复用了 `tools/blind_annotation.py::project` 的保守全任务证据门，以及 `tools/scoring/index.py` 的证据索引。现有 `okb/cli.py` 尚未注册新子命令；四层评分通过上表中的独立 Python 脚本调用。原有 `tools/score.py` 及正式报告入口未被替换。

## 3. 四层分别完成到哪里

| 层次 | 已实现 | 当前限制 |
|---|---|---|
| 证据可信检查 | 任务状态、对话覆盖、raw 来源、记忆扫描数量、文件内容指纹等校验；失败保留原因并拒判 | 复用保守门，不能据此宣称所有存储范围均已完整验证；有效空快照契约仍需解决 |
| 客观结果检查 | `file_exists`、`file_unchanged`、`json_field_equals`；严格 JSON、类型与字段值比较 | 尚非通用日期、集合、配置、操作禁令检查库；YAML 字段语义仍交判官处理 |
| 语义事实检查 | `supported / contradicted / insufficient`；限定 memory、file 或精确回复轮次；要求真实 ID、pointer、quote | 引用真实不证明理由正确，也不证明抗提示注入；当前需求命题是自然语言字符串，并非主体/极性/时间的结构化事实本体 |
| 校准与争议处理 | 失败、未决及新旧分歧进入复核队列；保留完整检查向量；不写人工答案 | 尚未实现代表性随机抽样与争议样本分开的完整校准闭环；两名真人及独立验证仍未完成 |

存在任何 mandatory 未决项时，整条任务保持 `unassessable`，即使已经观察到一个客观失败；失败仍保存在 `violated_checks` 中。所有必需检查可判后，按计划顺序选择失败类别；语义支持不能抹掉客观失败。

两题迁移包含评分口径调整，例如允许其他符合菜单与过敏要求的菜品、取消用户未要求的解释性回复关键词，并检查用户明确提出的安静座位偏好。因此未来的新旧差异不能全部解释成“同一评分标准下的误判修复”。

## 4. 历史证据核验与重跑

原始准备结果：`D:/a_shanghai/_review/four-layer/d1-prepare-001` 和 `robustness-prepare-001`。已校验完成标记中的 summary/results 哈希，以及输入锁：前者 262 项、后者 1 项，均与当前文件一致。

| 历史任务 | 第一层 | 语义请求数 | 最终状态 |
|---|---|---:|---|
| `openclaw-retain_allergy_001` | 通过 | 3 | 等待判官，未评分 |
| `openclaw-dual_order_001` | 通过 | 4 | 等待判官，未评分 |
| `hermes-retain_allergy_001` | 拒判：memory snapshot did not scan a file | 0 | 证据不足，未评分 |
| `hermes-dual_order_001` | 拒判：memory snapshot did not scan a file | 0 | 证据不足，未评分 |

故总数是 4 条任务、2 条证据有效、0 条 assessed、4 条 unassessable。33 案例为 0/33 有效回答；误杀率、漏判率、表达一致性等未测指标为 null，不能写成 0% 错误。当前等价改写统计仅有一个包含 11 案的等价组，不能外推为广泛表达鲁棒性。

2026-09-29 新准备结果位于 `D:/a_shanghai/_review/four-layer/takeover-20260929-r2/`。历史任务与反例两组的 results、requests、summary 均与上述旧准备结果逐字节一致。prepare 返回 1 表示尚无完整判定，属于当前预期；必须同时检查 `complete.json`，不能仅凭退出码判成功。

## 5. 本轮回归证据

- 29 套 Python 测试脚本、1 套 JavaScript 测试脚本均退出 0；四层专用测试实测 `Ran 43 tests ... OK`。
- 两个 prepare 入口均生成有效完成标记；总共 32 个执行步骤符合预期。
- 受保护的 10,970 个既有文件哈希未变；包含历史 runs、samples、评分代码、engine、schemas、deliveries 及预注册文件。
- Python 测试进程与其子进程设置了 socket connect / DNS 拒绝钩子；真实判官 HTTP 调用为 0。
- 日志仍有既有文件句柄未关闭的 `ResourceWarning`，未导致测试失败。本轮为 Windows 本地回归，不能代替 openKylin 部署验收或独立审查。

机器报告：`D:/a_shanghai/_review/four-layer/takeover-20260929-r2/summary.json`。每个步骤的命令标识、退出码、耗时及日志哈希均在报告内。

首轮使用文档工具附带 Python 时因缺 PyYAML 等依赖失败，保留在 `takeover-20260929/`，不作为通过证据。已按项目 `pyproject.toml` 将 PyYAML/jsonschema 安装到仓外的 `takeover-deps/` 后重跑，不改项目依赖声明。

## 6. 离线复现

从仓库根目录运行，Python >= 3.10，并安装项目声明的 PyYAML、jsonschema。下面输出目录必须全新且父目录存在；不要使用已存在的历史目录。

```bash
python tools/tests/test_four_layer.py
python tools/score_four_layer.py --paper build/paper-v0.2.jsonl --plan samples/four-layer-pilot.v1.json --run-dir runs/run_v02_d1 --out-dir ../_review/four-layer/d1-prepare-NEW
python tools/semantic_robustness.py --out-dir ../_review/four-layer/robustness-prepare-NEW
```

题面须与计划绑定的 SHA256 一致：`b5d71fd8c95c5385232fdf07604a1e9b8e072e0fa82105e0b8f37df95f357788`。缺 build 题面时按 README 从冻结的 `samples/pilot-v0.2.yaml` 重建并校验，不改写冻结输入来绕过门槛。

输出目录保存逐条结果、请求、输入锁、summary、完成标记；四层入口还输出 review-queue。请求及结果可能含完整任务证据，应继续按私有评测材料保管。

## 7. 接续顺序与在线边界

1. 先审阅并将这 7 个新增文件及本说明纳入本地版本管理；需要提交时使用仓库已有 `tools/gitcommit.py` 闸门。当前文档不宣称已提交、已推送或已在队友环境部署。
2. 在线准备：确认判官模型、端点、当前输入/输出/缓存单价、输出预算与金额上限，再向用户给出费用估算。旧配置和旧大批量预算不能直接当成本轮授权。
3. 当前请求规模为反例 33 + 历史请求 7 = 40 次；这是请求量，不是 token 数或金额。现实现不自动重试，失败请求计入 `max_calls`，但调用次数限制不是金额硬上限。
4. 在线客户端目前未设置输出 token 上限，且只保存有效判决，没有完整保留供应商 usage、返回模型标识及失败调用原始审计元数据。正式小额验证前应补齐必要的费用与调用审计能力，不能承诺已有可核算的费用硬闸。
5. 获得本轮模型和预算授权后，执行新批次；保留失败和缺响应，报告覆盖率、误杀/漏判、无依据裁决、语义弃判、调用缺失及等价组一致性。33 个作者案例属于开发集，不构成独立总体准确率证据。
6. 处理 Hermes 采集范围/有效空快照问题，再进入两名真人、小批真实闭环、六维报告与扩题。不要用英文关键词修补替代语义验证，也不要把缓存或预设答案回放当成新模型测量。
