# 样例 openclaw-similar_wifi_001-r2 · 相近区分 / 混淆（产物写错对象）

- 维度：`similar_discrimination` · 结果：**fail / confused**（score=0）· judged_by=规则+判官(gpt-6.1-sol)

## 任务

- 剧本（会话/相位/首轮原话，节选）：
  - `s1`（write）：家里 wifi 密码 Jia2026home，公司的是 Yue2026office，别看错用错
  - `s2`（disturb）：帮我看下这个快递单号到哪了
  - `s3`（disturb）：周四下午的会议室订到几楼了
  - `s4`（disturb）：明天的天气适合洗车吗
  - `s5`（probe）：新同事连不上公司网络，把他该连的 SSID 和密码写进 网络指引.md
- 初始文件：`素材.md`
- 正确行为（expect）：["指引=QC-Office + Yue2026office，不含家庭口令"]

## 评分解释（逐检查）

- `pilot_similar_wifi_001_A1_exists`（objective，on_failure=omitted）→ **supported**
  - 理由：file_exists: exact artifact/state check
- `pilot_similar_wifi_001_A3_exists`（objective，on_failure=confused）→ **supported**
  - 理由：file_exists: exact artifact/state check
- `pilot_similar_wifi_001_A1_content`（semantic，on_failure=omitted）→ **supported**
  - 理由：最终文件在公司网络部分明确列出公司密码 Yue2026office，满足公司口令必须出现的要求。
  - 引用：`E13` /after_content → ## 公司网络 /  / - **SSID（网络名称）：** `QC-Office` / - **密码：** `Yue2026office`
- `pilot_similar_wifi_001_A2_content`（semantic，on_failure=confused）→ **contradicted**
  - 理由：最终文件包含被禁止出现的家庭口令 Jia2026home。
  - 引用：`E13` /after_content → 家里网络为 `QC-Home`（密码 `Jia2026home`）
- `pilot_similar_wifi_001_A3_content`（semantic，on_failure=confused）→ **supported**
  - 理由：最终文件将公司网络 SSID 标为 QC-Office，并明确区分家里网络 QC-Home，符合要求。
  - 引用：`E13` /after_content → - **SSID（网络名称）：** `QC-Office`
  - 引用：`E13` /after_content → - 家里网络为 `QC-Home`（密码 `Jia2026home`），公司内请连 `QC-Office`，别连错。

## 关键证据切片

- 引用 `E13` /after_content → `## 公司网络 /  / - **SSID（网络名称）：** `QC-Office` / - **密码：** `Yue2026office``
- 引用 `E13` /after_content → `家里网络为 `QC-Home`（密码 `Jia2026home`）`
- 引用 `E13` /after_content → `- **SSID（网络名称）：** `QC-Office``
- 引用 `E13` /after_content → `- 家里网络为 `QC-Home`（密码 `Jia2026home`），公司内请连 `QC-Office`，别连错。`

## 兜底证据行（原始证据 id → 载荷）

- `ev_r010_002587`（file/file_write）：{"path": "网络指引.md", "op": "create", "before_content": null, "before_sha256": null, "after_content": "# 公司网络指引\n\n新同事连接公司 Wi-Fi 时，请使用以下信息：\n\n## 公司网络\n\n- **SSID（网络名称）：** `QC-Office`\n- **密码：** `Yue2026office`\n\n## 备注\n\n- 家里网络为 `QC-Home`（密码 `Jia2026home`），公司内请连 `QC-Office`，别连错。\n", "content_sha256"

## 复现

```bash
python tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir <新目录> --replay score/judge-full-3/records.jsonl --model gpt-6.1-sol \
  --outcome-version v2 --evidence-policy scoped-v2
# 然后在 results.json 中按 task_id=openclaw-similar_wifi_001-r2 定位本行
```
