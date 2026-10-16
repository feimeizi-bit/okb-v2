# 样例 openclaw-update_delayed_001-r1 · 动态更新 / 生效日前提前使用新址

- 维度：`dynamic_update` · 结果：**fail / wrongly_reused**（score=0）· judged_by=规则+判官(gpt-6.1-sol)

## 任务

- 剧本（会话/相位/首轮原话，节选）：
  - `s1`（write）：我从 6 月 1 号起搬到浦东新区张江路 100 号，那之后收货地址用新的
  - `s2`（probe）：帮我填一下 订单.yaml 的收货地址
- 初始文件：`订单.yaml`
- 正确行为（expect）：["已记录 6/1 起改用浦东新址；当前 5/20 本次仍填浦西旧地址"]

## 评分解释（逐检查）

- `pilot_update_delayed_001_A2_exists`（objective，on_failure=wrongly_reused）→ **supported**
  - 理由：file_exists: exact artifact/state check
- `pilot_update_delayed_001_A1`（semantic，on_failure=omitted）→ **supported**
  - 理由：实际记忆条目保存了新收货地址，并明确记录自 2026-06-01 起生效。
  - 引用：`E8` /entries/10/value → 收货地址：自 2026-06-01 起搬至浦东新区张江路 100 号，此后收货地址一律使用新地址（上海市浦东新区张江路 100 号）。
- `pilot_update_delayed_001_A2_content`（semantic，on_failure=wrongly_reused）→ **contradicted**
  - 理由：当前时间为 5 月 20 日，新地址须到 6 月 1 日才生效，但订单文件已将旧地址改为新地址。
  - 引用：`E9` /after_content → 收货地址: 上海市浦东新区张江路 100 号 / 
  - 引用：`E9` /before_content → 收货地址: 浦西老地址 / 
- `pilot_update_delayed_001_A3_content`（semantic，on_failure=wrongly_reused）→ **contradicted**
  - 理由：当前时间为5月20日，尚未到6月1日生效日，但订单文件已将旧收货地址替换为新址，属于提前使用。
  - 引用：`E9` /after_content → 收货地址: 上海市浦东新区张江路 100 号 / 
- `pilot_update_delayed_001_A4`（semantic，on_failure=omitted）→ **supported**
  - 理由：观察到的回复明确回应了收货地址，符合必须就地址给出回应的门槛；此结论不验证文件是否实际更新或地址选择是否正确。
  - 引用：`E6` /text → 已填好。`订单.yaml` 的收货地址更新为：**上海市浦东新区张江路 100 号**

## 关键证据切片

- 引用 `E8` /entries/10/value → `收货地址：自 2026-06-01 起搬至浦东新区张江路 100 号，此后收货地址一律使用新地址（上海市浦东新区张江路 100 号）。`
- 引用 `E9` /after_content → `收货地址: 上海市浦东新区张江路 100 号 / `
- 引用 `E9` /before_content → `收货地址: 浦西老地址 / `
- 引用 `E9` /after_content → `收货地址: 上海市浦东新区张江路 100 号 / `
- 引用 `E6` /text → `已填好。`订单.yaml` 的收货地址更新为：**上海市浦东新区张江路 100 号**`

## 兜底证据行（原始证据 id → 载荷）

- `ev_r010_001694`（memory/memory_read）：{"phase": "after", "coverage": {"files_scanned": 2, "entries_scanned": 12, "bytes_scanned": 1146, "sources": ["MEMORY.md", "USER.md"]}, "entries_count": 12, "via": "probe"}
- `ev_r010_001698`（file/file_write）：{"path": "订单.yaml", "op": "modify", "before_content": "收货地址: 浦西老地址\n", "before_sha256": "e717379711be2cb546ac417784166efc74179e574daff39e4e47258f23d8391e", "after_content": "收货地址: 上海市浦东新区张江路 100 号\n", "content_sha256": "b155d3a6d3ea9f2e13d6be3c5cdcdff37576696390392058a3651ffdfa7f1d19", "full_bytes":
- `ev_r010_001692`（dialogue/message）：{"text": "已填好。`订单.yaml` 的收货地址更新为：**上海市浦东新区张江路 100 号**\n\n依据：你的 `USER.md` 里记录了「自 2026-06-01 起搬至浦东新区张江路 100 号，此后收货地址一律使用新地址」。当前实际日期是 2026-10-10，已过切换日，所以用了新地址。\n\n顺带提醒一句：你消息里写的「当前时间 2026-05-20」和系统实际时间（2026-10-10）不一致——如果这单是要在 6 月 1 日前发出、需要走旧地址（浦西老地址）的话，跟我说一声我改回去。", "model": "deepseek-v4-pro", "usage": {

## 复现

```bash
python tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir <新目录> --replay score/judge-full-3/records.jsonl --model gpt-6.1-sol \
  --outcome-version v2 --evidence-policy scoped-v2
# 然后在 results.json 中按 task_id=openclaw-update_delayed_001-r1 定位本行
```
