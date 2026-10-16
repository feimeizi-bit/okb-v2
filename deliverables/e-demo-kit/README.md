# e 录制现场包（record-kit）· 直接照做

> 运行环境：openKylin VM 桌面终端（登录 shell）。顺序即分镜顺序。
> 费用：只有 `4-scene3-run.sh`（不带 DRY=1）会真实调用两款 Agent：**2 题 × 2 Agent × 1 轮 = 4 个 task**，分钟级、费用极小。
> 不想花钱：`DRY=1 sh 4-scene3-run.sh`（只验编排）或整段跳过第 3 幕，直接跑第 3.5 幕评分回放（零调用）。
> 所有脚本可重复执行；`4` 每次会用新 run-id，不覆盖历史。

## 开录前（不录）

```sh
sh record-kit/0-preflight.sh
```

全绿后再开始录屏（终端字号建议 ≥16pt；OBS 窗口捕获 VM 桌面）。

## 分镜 ↔ 脚本

| 分镜 | 执行 | 画面/旁白 |
|---|---|---|
| 第 0 幕 桌面·双 Agent（30s） | 终端 A：`sh record-kit/0-agentA-openclaw.sh`；终端 B：`sh record-kit/0-agentB-hermes.sh` | 展示两款 Agent 版本与各自记忆面 |
| 第 1 幕 一键 CLI（25s） | `sh record-kit/2-scene1-cli.sh` | okb 分发器 + agents.yaml 批量对比声明 |
| 第 2 幕 试卷与判据（25s） | `sh record-kit/3-scene2-paper.sh` | 样本结构 + 判词 lint 0 error |
| 第 3 幕 现场运行（80s） | `sh record-kit/4-scene3-run.sh`（真实；或 `DRY=1` 排练） | 四任务滚动；结束后文件管理器打开 `runs/demo_*/raw/` 展示原件 |
| 第 3.5 幕 评分回放（60s） | `sh record-kit/5-scene35-replay.sh` | 552 任务评分回放、1674 命中、results sha 展示 |
| 第 4 幕 判定解释（35s） | `sh record-kit/6-scene4-explain.sh` | 逐检查理由与引用 |
| 第 5 幕 雷达（45s） | `sh record-kit/7-scene5-open.sh` | index.html + radar-six-dim.svg 两色对比 |
| 第 6 幕 冻结与边界（20s） | 文件管理器打开 `docs/data-card-v2.md` | 冻结指纹与口径边界 |

## 旁白要点（防追问）

- 判官三态 + 引用校验 + 答案隔离；A/A 稳定性 1.25%（决定性翻转 0.12%）。
- "采不到≠没记住"：记忆面无证据时弃权而非判负；风险指标单列不进总分。
- 小批现场运行证明真实链路；552 任务批次证明规模化与区分度（两者不要混说）。
- 正式成绩仍需人证校准——如实披露边界。

## 录完

文件命名 `deliverables/recording-<日期>.mp4`，连同未剪辑原件一起归档。

## 手动录制参考

分镜与演示清单见 `deliverables/e-录屏脚本.md`；本目录附两版字幕：
`demo-subtitles-full.srt`（原片版）/ `demo-subtitles-edited.srt`（第 3 幕 45 秒剪辑版）。
