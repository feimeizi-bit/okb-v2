# engine — 考试流程引擎

> 负责人：A　状态：**证据采集层 v0.1 + 批量调度 v0.3（长跑保障版）已入库**（VM 冒烟 + C-2 全链回归通过）
> 长跑机制设计：`docs/A-longrun-resilience-design.md`

## 已实现模块

| 文件 | 职责 | 对齐接口 |
|---|---|---|
| `paths.py` | `field_path` 解析（与规则评分器共享同一实现） | CONTRACT §15 #8（已冻结，规则见 `docs/contract-a-confirm-v0.1.md` §8） |
| `redact.py` | PII 判定 / 替换（先判定后替换）+ `pii_hits` + argv 掩码 | I-12 / I-18 / B 复核 P0-1 |
| `workspace.py` | fixture 布置（占位符物化）/ 快照 / diff / realpath 路径安全 | I-10 / I-14 / CONTRACT §6 |
| `collectors.py` | `EvidenceWriter`：id/seq/ts/raw_ref、截断（头尾 32KB）、落 `evidence.jsonl`、**checkpoint/rollback/remove_tasks**（长跑回滚与证据手术） | I-1 / I-11 |
| `adapter.py` | 适配器基类：capabilities / reset / send / probe_memory / **watch_health** | I-2 / I-3 / I-4 |
| `adapters/openclaw.py` | OpenClaw 适配器（会话键、`--json` 回复抽取、记忆快照、reset、租约重试、embed-server 看门狗） | I-7 |
| `adapters/hermes.py` | Hermes 适配器（`chat -c` 会话续接、`-Q` 回复抽取、记忆快照、reset） | I-7 |
| `adapters/mock.py` + `mock_agent.py` | mock 合成考生（§14 独立子进程 + stdin/stdout JSONL，读 B 的策略文件，ops 为状态变更命令） | I-17 / CONTRACT §14 |
| `runner.py` | 批量调度（agents × samples × repeats，串行 + 健康检查 + 五态状态机）+ 单 task 生命周期 + manifest + **任务台账/续跑/熔断/重试/预算/磁盘守卫/优雅停机/分片** | I-5 / I-6 / I-9 / I-10 / CONTRACT §1 §8 |
| `timeanchor.py` | 时间锚定事后归一化：会话首轮注入剧本时间 + 文件 mtime / 记忆时间戳回写（不改进程时钟） | I-15 方案 2 |
| `merge_runs.py` | 分片 run 目录归并（证据全局重编号 + raw/台账/manifest 合并；先归并后评分） | 长跑保障（本仓设计） |

## 待开发

- `replay` 模式 + judge cache 稳定投影（I-13 / I-19）
- `action_map/<agent>.yaml`（I-7 抽象动作映射）

## 运行（VM 内，仓库根目录）

```bash
# 单样本冒烟（OpenClaw）
python3 -m engine.runner --agent openclaw --suite samples/pilot-v0.1.yaml \
  --sample-id boundary_pii_mask_001 --run-id run_20260921_001

# 批量：两款 Agent × 全部样本 × 2 次重复（串行）
python3 -m engine.runner --agent openclaw --agent hermes \
  --suite samples/pilot-v0.1.yaml --repeat 2

# 官方输入：读 B 的构建产物（锁 suite 版本，与评分器/C-2 工具链同源）
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx

# 长跑：跑前检查（磁盘/服务/paper sha）——不调用 Agent
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx \
    --expect-paper-sha <sha256> --preflight-only

# 长跑：断点续跑（跳过已完成任务；in-flight 任务回滚重开）
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx --resume

# 长跑：补跑失败样本（先删旧失败证据再重跑，防重复）
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx --resume --retry-invalid

# 分片（多机/多进程同一份 paper，各自 --shard I/N；跑完归并）
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx --shard 1/3
python3 -m engine.merge_runs --out runs/run_xxx runs/run_xxx.shard1of3 runs/run_xxx.shard2of3 runs/run_xxx.shard3of3

# 同一逻辑 run_id 多目录（跨产品并行 OpenClaw ∥ Hermes；或全量 ×1 与子集额外轮分段）
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx --run-dir runs/run_xxx.oc
python3 -m engine.runner --agent hermes   --paper build/paper.jsonl --run-id run_xxx --run-dir runs/run_xxx.hm
python3 -m engine.merge_runs --out runs/run_xxx runs/run_xxx.oc runs/run_xxx.hm

# VM 长跑包装（tmux/nohup，掉线不中断）：见 deploy/run-batch.sh
bash deploy/run-batch.sh run_xxx --agent openclaw --paper build/paper.jsonl

# mock 合成考生（C-2 对拍；策略文件由 B 提供）
python3 -m engine.runner --agent mock --mock-policy samples/mock-policies/remember.v0.1.yaml \
    --paper build/paper.jsonl --run-id mock_xxx

# 只打印计划与能力预判（不调用 Agent）
python3 -m engine.runner --agent openclaw --suite samples/pilot-v0.1.yaml --dry-run

# 产物
runs/<run_id>/
├─ manifest.json      # 运行元信息 + agents[] + tasks[] 汇总（含各 type 事件计数）
├─ evidence.jsonl     # 证据流水（schemas/evidence.schema.json）
├─ raw/<task_id>/     # 原始请求/响应、记忆快照、workspace 清单、系统动作
├─ tasks.jsonl        # 任务台账（start/finish + 证据回滚点；续跑依据）
├─ run.log            # 结构化运行日志（JSONL，可 tail -f）
├─ preflight.json     # 跑前检查报告
├─ usage.json         # token 用量汇总（per task + total）
└─ verdicts.jsonl     # B 评分器写入（A 只创建空占位）

## 长跑保障（CLI）

| 参数 | 默认 | 作用 |
|---|---|---|
| `--resume` | 关 | 按 `tasks.jsonl` 续跑（done 跳过 / in-flight 回滚重开） |
| `--retry-invalid` | 关 | 续跑时把 invalid 终态任务重开（证据手术：删旧行 + 重编号） |
| `--task-retries` | 1 | 任务级重试次数（重试前回滚证据） |
| `--max-consecutive-invalid` | 3 | 连续 invalid 熔断（0=关） |
| `--max-tokens` | 无 | token 预算，超限优雅停机 |
| `--min-free-mb` | 512 | 磁盘剩余下限（MB） |
| `--health-interval` | 300 | 轮间看门狗间隔（OpenClaw 查 embed-server） |
| `--shard I/N` | 无 | 确定性分片（配合 `engine.merge_runs`） |
| `--run-dir` | 无 | 覆盖 run 目录：同一逻辑 run_id 多目录（跨产品并行/分段跑批），跑完归并 |
| `--preflight-only` | 关 | 只做跑前检查 |
| `--expect-paper-sha` | 无 | 校验 paper sha256（防跑错 suite） |

退出码：`0` 全 ok / `1` 有 invalid / `2` 启动失败（锁/参数）/ `3` 守卫停机（熔断/预算/磁盘/预检）/ `130` 信号中断。
```

## 注意

- **OpenClaw 的文件工具固定写 agent workspace**（`~/.openclaw/workspace`），不是进程 cwd；文件类 fixture / 证据以适配器 `file_root()` 为准（实测校准，见 `docs/contract-a-confirm-v0.1.md` §3）
- 时间锚定为**方案 2**：不改进程时钟（避免 TLS 证书有效期冲突），对话首轮注入 + 会话后归一化文件/记忆时间戳

## 测试（无需第三方依赖）

```bash
python engine/tests/test_paths.py
python engine/tests/test_redact.py
python engine/tests/test_workspace.py
python engine/tests/test_runner.py
python engine/tests/test_resilience.py   # 分片/台账/回滚/锁/熔断/归并（宿主可跑）
```

VM 内另有 `test_timeanchor.py` / `test_adapter.py` / `test_mock.py`（需要 pyyaml）。
