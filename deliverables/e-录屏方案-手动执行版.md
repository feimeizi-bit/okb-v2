# e · 录屏方案（手动执行版）

> 用途：你自己在 VM 桌面上照着录，全程约 15 分钟（含真实运行）。
> **强烈建议**：录完后把第 3 幕变速剪辑到 ~45 秒 → 成片 ≈ 4 分 50 秒，正好落在赛题"建议 3–5 分钟"。
> 字幕文件：`demo-subtitles-full.srt`（不剪辑用）/ `demo-subtitles-edited.srt`（剪辑推荐用，第 3 幕按 45 秒预算）。

## 0. 录前准备（约 5 分钟，不录）

1. **VM 到桌面**：已配置自动登录，开机直接进桌面。
   如果桌面无响应：VirtualBox 菜单 → 关闭 → 强制关机，再启动。
2. 终端预检（开始菜单搜"终端"或 Ctrl+Alt+T）：
   ```sh
   cd /home/okb/okb && sh record-kit/0-preflight.sh
   ```
   应全绿：openclaw / hermes 版本、`embed-server: active`、`relay.env: present`、演示件齐备。
3. 第 3.5 幕数据自检：`ls /tmp/demo-replay` —— 应有 6 个文件。
   **若为空或缺失**：先跑附录 A 的重建命令。
4. **先不录，干跑一遍**（2 分钟）：依次运行下面分镜表里的每条命令，看一眼输出正常，再关掉窗口。
   第 3 幕干跑用：`DRY=1 sh /home/okb/demo_driver/scene3.sh`（不调用 Agent、不花钱）。
5. 关闭所有多余窗口；终端字号已调好（13pt）。
6. **开始录制（宿主机）**：双击 `deliverables/e-demo-kit/host/record-start.ps1`。
   （也可用 Win+G 或 OBS；本方案按 VBox 内置录制写。VBox 录制无声音——字幕即旁白。）

## 1. 分镜执行表（顺序即分镜；每幕做完关掉该窗口再开下一幕）

| 幕 | 在 VM 里执行 | 屏幕预期 | 建议时长 |
|---|---|---|---|
| 0 · 双 Agent 就位 | **终端 A**：`sh /home/okb/demo_driver/scene0a.sh`；**终端 B**：`sh /home/okb/demo_driver/scene0b.sh` | A 显示 OpenClaw 版本+工作区；B 显示 Hermes 版本+记忆面 | ~30s |
| 1 · 一键 CLI | `sh /home/okb/demo_driver/scene1.sh` | okb 分发器 + agents.yaml 批量对比声明 | ~25s |
| 2 · 试卷与判据 | `sh /home/okb/demo_driver/scene2.sh` | 样本结构 + 判词 lint `0 error` | ~25s |
| 3 · 现场运行（**真实调用**） | `sh /home/okb/demo_driver/scene3.sh` | 四任务滚动输出；结束打印 `runs/demo_*/raw` 列表 | **8–12 分钟（记下实际时长 T3）** |
| 3.5 · 评分回放（零调用） | `sh /home/okb/demo_driver/scene35.sh` | 回放命令 + 产物清单 + `cache_hits 1674` + 指纹对照 | ~40s |
| 4 · 判定可解释 | `sh /home/okb/demo_driver/scene4.sh` | wifi 题逐检查结论+理由+引用 | ~30s |
| 5 · 六维雷达 | `xdg-open score/final-report-r12/radar-six-dim.svg`（先 `cd /home/okb/okb`） | 雷达图全屏/窗口显示 | ~40s |
| 6 · 冻结与边界 | `sh /home/okb/demo_driver/scene6.sh` | 数据卡冻结指纹表 | ~25s |

- 备用：若 `/home/okb/demo_driver/` 不存在，直接用 record-kit 原脚本（见附录 B）。
- 第 3 幕注意：运行期间**不要开关其他窗口**；某题卡住也别慌，等超时机制自己收尾。
  若明显异常（>5 分钟无新输出），可 Ctrl+C 中止本幕，停录后重录这一幕。
- 第 5 幕兜底：`xdg-open` 打不开就改用 `xdg-open score/final-report-r12/index.html`；
  再不行：文件管理器进入 `score/final-report-r12/` 双击 `radar-six-dim.svg`。

## 2. 停止与归档

1. 宿主机双击 `record-stop.ps1`；原片在 `deliverables/recording-<时间>-screen0.webm`。
2. 命名规范：成片 `deliverables/recording-<日期>.mp4`；**保留原片与本次录屏日志**。

## 3. 字幕怎么用

- **不剪辑**（原片 ≈15 分钟）：VLC 打开原片 → 字幕菜单 → 添加 `demo-subtitles-full.srt`（同名放一起会自动加载）。
- **推荐剪辑**：把第 3 幕（约 01:33 起、时长 T3）变速到 45 秒左右；用 `demo-subtitles-edited.srt`。
  若你剪出来的第 3 幕不是 45 秒：从该文件第 5 条起整体平移差值即可；**或把实际 T3 发我，我重算一版精确对齐的**。
- **烧录字幕（可选）**：在 VM 里做（ffmpeg 已装）。把 webm + srt 放进 VM（共享文件夹或 /tmp）后：
  ```bash
  ffmpeg -i 原片.webm \
    -vf "scale=1920:1080:flags=lanczos,subtitles=字幕.srt:force_style='FontName=Noto Sans CJK SC,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=2'" \
    -c:v libx264 -crf 20 -pix_fmt yuv420p 成片.mp4
  ```
  （不要 1080p 就删掉 `scale=1920:1080:flags=lanczos,`。）

## 4. 附录 A：重建第 3.5 幕展示件（/tmp/demo-replay 缺失时）

```sh
mkdir -p /tmp/demo-replay
cd /home/okb/okb
cp score/judge-full-3-replay/complete.json score/judge-full-3-replay/input-lock.json \
   score/judge-full-3-replay/results.json score/judge-full-3-replay/review-queue.json \
   score/judge-full-3-replay/semantic-requests.json score/judge-full-3-replay/summary.json \
   /tmp/demo-replay/
ls -l /tmp/demo-replay
```
说明：这些就是从"冻结回放目录"取产物；视频里展示的 cache_hits 与指纹均来自这里，属实。

## 5. 附录 B：demo_driver 不存在时的完整替代命令

```sh
cd /home/okb/okb
sh record-kit/0-agentA-openclaw.sh     # 幕 0-A（另开一个终端跑 0-agentB-hermes.sh）
sh record-kit/2-scene1-cli.sh          # 幕 1
sh record-kit/3-scene2-paper.sh        # 幕 2
sh record-kit/4-scene3-run.sh          # 幕 3（真实；DRY=1 前缀为干跑）
sh record-kit/6-scene4-explain.sh      # 幕 4
sed -n '1,40p' docs/data-card-v2.md    # 幕 6（数据卡）
```
第 3.5 幕用附录 A + 下面这段打印：

```sh
python3 - <<'PY'
import hashlib, json
s = json.load(open('/tmp/demo-replay/summary.json', encoding='utf-8'))
print('任务:', s['tasks'], '| assessed:', s['assessed'], '| unassessable:', s['unassessable'])
print('判官记录命中 cache_hits:', s['cache_hits'], '| extra:', s['extra_replay_records'])
sha = hashlib.sha256(open('/tmp/demo-replay/results.json', 'rb').read()).hexdigest()[:16]
print('results sha256:', sha, '| 冻结对照: afc8b73a26cba085 →', '一致' if sha == 'afc8b73a26cba085' else '不一致')
PY
```

## 6. 数据一致性提醒

字幕里的数字（1674 / extra 0 / afc8b73a…）与第 3.5 幕屏幕输出一一对应。
如果你屏幕上的输出与此不符，**先别继续录**，把该幕截图发我核对。

## 7. 字幕全文（分镜对照，可直接改）

| 对应画面 | 字幕（两行以内） |
|---|---|
| 开场桌面 | openKylin 桌面环境 · 长期记忆评测现场 |
| 幕 0 | 被测两款通用智能体：OpenClaw 与 Hermes，各自拥有独立工作区与记忆面 |
| 幕 1 | 一键 CLI：换被测系统只改 agents.yaml（命令模板 + 能力声明） |
| 幕 2 | 92 道多会话记忆题 · 六维配额；判词阻断级 lint：0 error |
| 幕 3 开头 | 现场运行：2 题 × 2 Agent × 1 轮（真实调用） |
| 幕 3 中段 | 清场 → 布置 → 前后记忆快照 → 逐轮独立进程调用 → 文件差分 |
| 幕 3 后段 | 原始输出逐轮落盘：证据不是转述，是可审计的原件 |
| 幕 3 结尾 | 运行完成后可在 runs/demo_*/raw/ 查看证据原件 |
| 幕 3.5 | 评分回放（零调用）：552 任务 / 1674 条判官记录 |
| 幕 3.5 尾 | cache_hits 1674 · extra 0；评分指纹与冻结版一致 |
| 幕 4 | 可解释判定：结论 + 理由 + 证据引用（判官不可见答案标签） |
| 幕 5 | 六维对比雷达：hermes vs openclaw；覆盖率不足维度只进雷达、不进正式总分 |
| 幕 6 | 冻结口径：数字绑定指纹与边界说明；正式成绩仍需人证校准（如实披露） |
| 片尾 | okb · openKylin 智能体长期记忆自动化评测 |
