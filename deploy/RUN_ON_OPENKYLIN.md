# openKylin VM 执行单（照抄即用）

> 目标：在 openKylin 3.0 上把主链路一次跑完 —— 真实智能体运行 → 证据采集 → 自动评分 →
> 报告 → 六维雷达图。本单只描述**已取得本机验证**的命令；凡未在本机验证过的步骤都写明「待 VM 验证」。

## 0. 前提

| 项 | 要求 | 依据 |
|---|---|---|
| 系统 | openKylin 3.0 (huanghe)，Python 3.12 | `docs/SETUP_VM_AGENTS.md` |
| 依赖 | `python3-yaml`、`python3-jsonschema`（≥ 声明版本） | `pyproject.toml`；`bash deploy/vm-deps.sh` 自检 |
| 智能体 | OpenClaw、Hermes 已安装并初始化 | `deploy/README.md` 使用顺序 1-4 |
| 代码分支 | `integration`（含 A 侧 F1 显式 LF + 主链路改动） | 本仓 git |

```bash
python3 -c "import yaml, jsonschema; print(yaml.__version__, jsonschema.__version__)"
bash deploy/vm-deps.sh
```

## 1. 把代码送到 VM（二选一）

```powershell
# 宿主机（VM 内无 git 凭据时）：scp 同步
powershell -ExecutionPolicy Bypass -File deploy\sync-to-vm.ps1
```

```bash
# 或 VM 内直接取分支（需 A 侧已把 integration 推到 gitee）
git fetch origin integration && git checkout integration && git pull --ff-only
```

注意：`build/`、`runs/`、`.env` 被 .gitignore 排除，**试卷必须在 VM 内重建**（下一步）。

## 2. 重建试卷（与评分器同源）

```bash
cd ~/okb
python3 tools/build_paper.py samples/paper-r1.yaml -o build/paper-r1.jsonl
python3 -m okb validate build/paper-r1.jsonl
sha256sum build/paper-r1.jsonl      # 期望 0da837550437684b…；回传该指纹，plan 绑定它
```

`samples/four-layer-r1.json` 是 100 题的四层评分计划（绑定 paper sha256）；它由 `python3 tools/freeze_candidates.py --extra-suite samples/v1.0-batchA.yaml --allow-restart --include-long-wait --allow-time-mode cross_session_only --allow-time-mode actual_elapsed --allow-time-mode logical_calendar_reference --target-samples 100` 复算产出（重跑逐字节一致）。

## 3. 主链路一次跑完

```bash
cd ~/okb
python3 -m okb run \
  --agents deploy/agents.yaml \
  --paper build/paper-r1.jsonl \
  --plan samples/four-layer-r1.json \
  --run-id okb_openkylin_001 \
  --repeat 3 \

# 100 题 × 2 Agent × 3 轮 = 600 任务；首轮可先 --repeat 2 验通
  --verify-traces
```

- 默认评分口径已是 `--outcome-version v2 --evidence-policy scoped-v2`（分层证据门）。
- 默认**不调用任何付费判官**：评分停在 prepare（客观层出判定，语义层出「待判请求」）。
- 规模口径：`--sample-id` 缺省即跑 plan 内全部 100 题；`--repeat 3` ⇒ 600 任务。建议先 `--repeat 1` 抽 4 题验通链路，再放全量（`--repeat` 也可写在 `deploy/agents.yaml`）。
- 阶段隔离：任一阶段失败会记录在 `run-summary.json`，前面已完成的产物不会丢。

跑完应得到：

```
build/run-okb_openkylin_001/
├── run-summary.json     ← 单一入口：每阶段状态、证据等级、产物路径
├── score/               ← results.json / summary.json / semantic-requests.json / review-queue.json
├── report/              ← report.json + index.html（含证据等级表）
└── radar.svg / radar.json   ← 六维雷达（缺失维度标注「缺失/insufficient」，不填 0）
```

只想重跑评分与报告（不重跑智能体）：

```bash
python3 -m okb run --skip-run --run-id okb_openkylin_001 \
  --paper build/paper-r1.jsonl --plan samples/four-layer-r1.json --verify-traces
```

## 3.5 预登记与跨轮汇总（**跑批之前**签发，不能事后补）

`report-multirun` 只接受跑批**前**封存的登记文件。登记里的 Agent 身份（`adapter_version` /
`executor_version` / `config_sha256`）是**代码派生的指纹**，必须与跑批同机同版本——改一行代码
就对不上，这是有意的 fail-closed（防"登记 A 代码、跑 B 代码"）。所以这两步必须在 VM 上做：

```bash
# 跑批前：签发登记（题目 / 计划 / Agent 身份 / 任务表），顺手产出导入清单
python3 -m okb register-run \
  --paper build/paper-r1.jsonl --plan samples/four-layer-r1.json \
  --agents deploy/agents.yaml --run-id okb_openkylin_001 --repeat 3 --mode prepare \
  --out build/run-okb_openkylin_001/registration.json \
  --emit-imports build/run-okb_openkylin_001/imports.json \
  --runs-dir runs --score-root build \
  --allow-missing-imports          # 此刻还没跑批，允许目录不存在
# 输出里的 registration_sha256 就是这次对比的凭证：回传它，并在 VM 之外另存一份

# 跑批 + 评分完成后：按登记汇总多轮
python3 -m okb report-multirun \
  --registration build/run-okb_openkylin_001/registration.json \
  --registration-sha256 <上一步的 registration_sha256> \
  --imports build/run-okb_openkylin_001/imports.json \
  --paper build/paper-r1.jsonl --plan samples/four-layer-r1.json \
  --out-dir build/run-okb_openkylin_001/multirun
```

注意：
- 登记会同时打印 `run_command`：**跑批必须用它那份样本集**（计划是考卷子集时它会带上
  `--sample-id`），否则任务表对不上，汇总会报 `batch task coverage mismatch`。
- `--emit-imports` 按 `<score-root>/<run_id>/score` 约定生成路径；不是这个位置就直接改
  `imports.json`（其中路径相对 `imports.json` 自身解析）。
- 独立多轮用 `--repeat N`（同一 run 内 r1..rN 都是主比较单元）；多给几个 `--run-id` 只用于
  **基础设施失败后的重跑留档**（同单元必须 `retry_of` 串联，主指标只看主尝试）。
- 导入清单允许指向未跑的批次：缺批**保留分母**，不会被悄悄缩掉。
- 手动评分（§4）时 scoped 证据必须配 `--outcome-version v2`，否则会报
  `scoped evidence requires v2 outcomes`。

## 4. 语义判官（花钱的那一步）

现状（本机已实测）：

- `--live` 的 legacy 两桶预算**拒绝**为 provider 返回的 `cache_write_tokens` 计价（防止低估费用的既有不变量，
  由 `tools/tests/test_cache_billing.py::test_legacy_budget_never_prices_positive_cache_writes_as_plain_input` 锁定）。
  CDN 网关从第 2 次调用起就会返回该字段 → 批次立刻 halt。
- 四桶 profile（`openai-cache-chat-v1`）在代码里被硬绑到 `gpt-6.1-sol`；本次实测的网关对
  `max_completion_tokens` / `service_tier` / `prompt_cache_options` 直接返回 HTTP 错误。
- 因此本机走的是「采集判官响应 → `--replay` 精确回放评分」这条**仓内既有的离线正路**：
  评分管线不变，只是花费记账落在仓外脚本。要复现：

```bash
# VM 内（待 VM 验证：本机在 Windows 上验证过）
python3 D:/a_shanghai/_scratch/fetch_judge.py build/run-okb_openkylin_001/score <out-dir> gpt-6-sol
python3 -m okb score-vnext --paper build/paper-r1.jsonl --plan samples/four-layer-r1.json \
  --run-dir runs/okb_openkylin_001 --out-dir build/run-okb_openkylin_001/score-live \
  --outcome-version v2 --evidence-policy scoped-v2 --replay <out-dir>/replay-records.jsonl --model gpt-6-sol
```

判官传输已修：显式 `User-Agent`（`urllib` 默认 UA 会被 CDN 以 403 / Cloudflare 1010 拒绝，
Windows 与 openKylin 表现一致）。可用 `OKB_JUDGE_USER_AGENT` 覆盖。

## 4.5 真机跑完后：让人复核"仪器自己"（两条命令）

mock 上出这种题没意义（要验的是真实 Agent 的记忆面会不会落在我们的观测范围之外），所以这一步
必须在 VM 上做，跑完归因再出题包：

```bash
# 1) 对真实跑批证据算归因（计划必须含 memory_before 检查项，即 r2；r1 会如实拒绝）
python3 -m okb baseline-attribution --run-dir runs/<run-id> \
  --plan samples/four-layer-r2.json --out build/run-<run-id>/attribution.json

# 2) 用归因结果出**盲题包**：题面只有观测事实，我们自己打的旗标封在 flags.private.json
python3 -m okb annotate-checks make-harness-worklist \
  --attribution build/run-<run-id>/attribution.json \
  --run-dir runs/<run-id> \
  --out-dir handoff/harness-<run-id> --count 30
```

为什么要它：判官回路只能证明"自动评分像人"；**像人的仪器也可能在量一个空的东西**。
这条回路让两个没看过我们结论的人各自解释同一条观测，再和我们的旗标对一遍，
给出 κ 与精确下界——这是"仪器测的是它声称的东西"的直接证据。

题包必须**盲**：`attribution / flags / suggested_check / counts_toward_score / instrument_label`
一律不进题面（有结构性测试保证）；`flags.private.json` 留在你（A）手里，双方交卷后才拿来算。

回传时请一并给：`attribution.json`（含 `flag_counts` 与 `review_queue_size`）、
`handoff/harness-<run-id>/worklist.jsonl` 与两份已 closed 的标注文件；
**`flag_counts` 非 0 时不要删**——那是我们发现观测面漏扫/跨任务残留的第一手证据，
审计器会要求文档对此作出解释。

## 5. 需要回传的回执

1. `build/run-<run-id>/run-summary.json`
2. `score/summary.json`（看 `evidence_valid` / `assessed` / `evidence_tiers` / `task_outcomes`）
3. `score/results.json` 中任一 fail 任务的 `layer3` 片段（含 `rationale` + `citations` + `evidence_tier`）
4. `report/index.html`、`radar.svg`
5. `runs/<run-id>/manifest.json` 与 `sha256sum build/paper-r1.jsonl`
6. `build/run-<run-id>/registration.json` 与同名 `.sha256` 旁证（预登记凭证）
7. `build/run-<run-id>/multirun/report.json`（看 `planned_batches` / `imported_batches` / `missing_batches`）
8. `openkylin-verify-receipt.json`（安装态自检 24 项，应 `failed: 0`）与 `sha256sum okb_*.deb`
9. `build/run-<run-id>/attribution.json` + 盲题包（§4.5：`flag_counts` / `review_queue_size`）
10. `python3 tools/run_suite.py` 的输出末几行。**VM 树上的预期：76 套件 / 967 用例 / 失败 0 /
    跳过 3**（无 node 的 JS 套件 + 两条需要维护者产物 `runs/`、`build/` 的套件，会如实标 SKIP，
    不算失败也不算通过；SKIP 清单会自己列名）。若出现任何 **FAIL**，那是缺陷，请把该套件输出的
    最后 20 行一起回传——上一轮 7 条 FAIL 里有 5 条其实是"缺输入裸抛异常"或"跑器误判"，
    已在本轮修成 SKIP/正判，所以现在的 FAIL 一律当真
   已修）；有 node 的机器上 JS 套件必须真跑并 PASS，跳过不等于通过）
11. 记忆证据实际落在哪一档：`evidence_tiers` 若只有 `T1_declared_snapshot`，说明真实采集没产出
   观测回执 / 全文采集 —— 这是**需要在本机之外补的第一手缺口**

## 6. 已知待办（不影响本单命令可跑）

- `.deb` 交付 d：`deploy/build-deb.sh` 已在 WSL2/Ubuntu 24.04 实测 `dpkg -i`、`dpkg -V` 无差异、
  `openkylin-verify.sh` 24 项全绿（含交付卷 100 题 × 2 轮 = 400 任务的采集→评分→报告→雷达→
  预登记→跨轮汇总）。**根目录仍无 `LICENSE`**（dpkg warn），权利人未定，属未办事项；
  本轮新增 `tools/register_run.py` 等文件后需重新构建一次再安装验证。
- 第三适配器（KylinBot / kylin-agent）未接：任务书点名的这两个智能体当前无 adapter，
  `engine/runner.py --agent` 的可选名仍是 openclaw/hermes/mock。
- `deploy/embed-server/server.py` 的 usage 两字段仍是写死 0，与真实观测无关，不能当计费证据。
