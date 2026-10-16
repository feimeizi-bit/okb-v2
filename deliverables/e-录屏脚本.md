# 交付 e · 演示视频录制脚本（分镜 + 演示清单）· v2

> **现场可执行包**：`deliverables/e-demo-kit/`（已同步到 VM：`/home/okb/okb/record-kit`；
> 预检、逐幕脚本、旁白提示全套；第 3 幕支持 `DRY=1` 排练）。分镜说明见下。

> 赛题要求（原文）："提供在 **openKylin 桌面环境下**，针对**至少两款不同智能体运行评测**，
> 并**产出多维对比雷达图**的**完整录屏（建议 3–5 分钟）**。"
> 本脚本四条硬要求逐一落位：第 0 幕（桌面环境+双 Agent）→ 第 3 幕（两款智能体现场运行）
> → 第 4–5 幕（评分+六维对比雷达）→ 全程 ≈4 分 40 秒。
> 命令已在本仓验证；口径与数字见 `docs/data-card-v2.md`。

## 录制工具（三选一，按省事程度排序）

| 方案 | 安装 | 内容 | 声音 | 操作 |
|---|---|---|---|---|
| **A. VirtualBox 内置录像（推荐）** | 零安装（已实测） | 只录 VM 桌面，画面最干净 | 无（可后期补旁白/字幕） | 宿主机双击 `e-demo-kit/host/record-start.ps1` → 按分镜操作 → `record-stop.ps1`；产物 `deliverables/recording-<时间>-screen0.webm` |
| B. Windows 游戏栏 | 零安装（组件已确认存在） | 录 VM 窗口（前台） | 可带麦克风，一条过 | 点开 VM 窗口按 **Win+G** → 捕获→开始录制（或 **Win+Alt+R**）；产物在 `视频\捕获` |
| C. OBS Studio | 自行下载（obsproject.com，~150MB） | 窗口捕获最专业，可多场景 | 可混音 | 来源→窗口捕获→选 `openKylin-V3.0`；输出 1080p |

- 需要 **mp4**：装 ffmpeg（`winget install ffmpeg`）后 `ffmpeg -i in.webm -c:v libx264 -crf 20 out.mp4`；WebM 本身也能直接播放。
- 建议：**A + 手机/录音软件单独录旁白**，或 **B 一条过带旁白**——赛题只要求"完整录屏"，音频不是硬要求。

## 录制前检查（不录屏，5 分钟）

1. openKylin **桌面模式**启动；两款 Agent 可调用：`openclaw --version`、`hermes --version`；
2. embed-server 存活（OpenClaw 记忆前提）：`systemctl is-active embed-server` → `active`；
3. **Agent 的模型接入正常**（relay 凭据在 `~/.okb/relay.env`，只引用不展示内容）；
4. 录屏工具（OBS/系统录屏），1080p；终端字号 ≥16pt；桌面干净、无隐私窗口；
5. 预算：第 3 幕只跑 **2 题 × 2 Agent × 1 轮 = 4 个 task**（分钟级；费用极小）。
6. **零调用替代**：若完全不想现场跑 Agent，把第 3 幕替换为 3′（已有 552 任务证据的回放），
   但旁白必须如实说明"这是回放采集结果的评分"，不要暗示为现场运行。

## 分镜（总 ≈ 4 分 40 秒）

### 第 0 幕 · 桌面环境与双 Agent（30s）【要求①】
- 画面：openKylin 桌面 → 打开两个终端：
  - 终端 A：`openclaw --version`，`ls ~/.openclaw/workspace | head`
  - 终端 B：`hermes --version`，`ls ~/.hermes/memories`
- 旁白："openKylin 桌面环境下，两款通用智能体已就位；它们各自有自己的工作区与记忆面。
  okb 在同一台机器上对两者做同一套长期记忆评测。"

### 第 1 幕 · 一键 CLI 与批量对比配置（25s）
```bash
okb --version && okb --help | head -16
sed -n '1,24p' deploy/agents.yaml
```
- 旁白："okb 是薄分发器，一条命令跑完整链路；不同智能体在 agents.yaml 里声明（命令模板+能力），
  换被测系统只改这一个文件。"

### 第 2 幕 · 试卷与判据防线（25s）
```bash
python3 -c "import json; d=[json.loads(l) for l in open('v2/01-paper-v2/_paperv2.jsonl',encoding='utf-8')][0]; print(d['id'], d['dimension'], len(d['script']),'sessions')"
python3 tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json
```
- 旁白："92 道多会话记忆题、六维配额；判词有阻断级 lint（极性/自足性），坏判据进不了考场。"

### 第 3 幕 · 两款智能体现场运行评测（80s）【要求②】
```bash
okb run --agents deploy/agents.yaml \
  --paper v2/01-paper-v2/_paperv2.jsonl --plan v2/01-paper-v2/paper-v2.plan.json \
  --sample-id "similar_wifi_001,update_delayed_001" --repeat 1 \
  --run-id demo_$(date +%m%d_%H%M) --out-root build/demo_live --skip-score
```
- 画面：终端滚动（openclaw 题 1 → openclaw 题 2 → hermes 题 1 → hermes 题 2）；
  结束后用文件管理器打开 `runs/demo_*/raw/`，点开 `s1_t0_stdout.txt` 与 `memory_before/after.jsonl`：
- 旁白："每个任务：清场→布置→前后记忆快照→逐轮独立进程调用→文件差分；
  原始输出逐轮落盘——证据不是转述，是可审计的原件。"
- （若走零调用替代 3′：直接展示 `runs/v2r3_20261010-healed/raw/` 的原件，并说明出处。）

### 第 3.5 幕 · 评分回放（完整批次的自动评分，60s）
```bash
python3 tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir /tmp/demo-replay --replay score/judge-full-3/records.jsonl \
  --model gpt-6.1-sol --outcome-version v2 --evidence-policy scoped-v2
```
- 旁白："评分器对 552 个真实任务逐条判定：证据门→客观检查→判官三态→聚合；
  1674 条判官记录零调用回放，与原始评分逐字节一致——评分链本身可复现。"

### 第 4 幕 · 判定可解释（35s）
```bash
python3 -c "
import json
r=json.load(open('score/final-report-r12/report.json',encoding='utf-8'))
t=[x for x in r['tasks'] if x['task_id']=='openclaw-similar_wifi_001-r2'][0]
print(t['task_outcome'], t['error_class'])
[print(c['id'], c['result'], '|', (c.get('rationale') or '')[:60]) for c in t['checks']]"
```
- 旁白："每条判定有理由和引用（证据 id + 位置 + 原文）；判官看不到答案标签；
  同题三次重复分别落 pass/fail/pass，说明评测的是行为而不是运气。"

### 第 5 幕 · 六维对比雷达图（45s）【要求③】
- 画面：浏览器打开 `score/final-report-r12/index.html`，再打开 `radar-six-dim.svg`；
  镜头指到两色多边形对比。
- 旁白："六维能力对比：长期保持上 hermes 39/39、openclaw 18/38，差异清晰；
  动态更新与任务复用是两者共同短板。同时给出覆盖率与'不可判按失败'的下界口径；
  覆盖率不足的维度只进雷达、不进正式总分——这是评测的诚实边界。"

### 第 6 幕 · 冻结与边界（20s）
- 画面：`docs/data-card-v2.md` 顶部（冻结清单）。
- 旁白："全部数字绑定冻结指纹与口径说明；正式成绩还需要人证校准——这正是下一步。"

## 旁白要点（防追问）

- 判官三态 + 引用校验 + 答案隔离；A/A 稳定性 1.25%（决定性翻转 0.12%）。
- "采不到≠没记住"：记忆面无证据时弃权而不是判负。
- 五分类含两类风险指标（错误持久化/错误复用），单列不进总分。
- 小批现场运行 = 证明真实链路；556 任务批次 = 证明规模化与区分度（两者不要混说）。

## 产出归档

录屏命名 `deliverables/recording-<日期>.mp4`；保留未剪辑原件与旁白稿。
