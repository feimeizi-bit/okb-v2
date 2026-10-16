# 交付索引（赛题 a–e · v2.0-R12 冻结版）

| 交付 | 文件/目录 | 状态 |
|---|---|---|
| **a 方案说明文档** | `a-方案说明文档.md` | ✅ 含"数据生成方式"全节 + 覆盖矩阵 + 评分流程 + 限制 |
| **b 样例数据与结果** | `b-样例包.md` → `v2/07-deliverable-b/`（3 案例：任务+证据+评分解释） | ✅ |
| **c 可复现材料** | `c-复现说明.md`；openKylin 回执 `c-openkylin-verify/receipt.md` | ✅ 含 VM 逐字节重放证据 |
| **d 工具化封装** | `d-工具封装.md`；`d-package/okb_0.1.0_all.deb`（content.sha256 + members） | ✅ openKylin 实测安装可用 |
| **e 演示视频** | `recording-2026-10-16.mp4`（4:39 · 1080p · 烧录中文字幕；含双 Agent 真实运行与实时监视双窗）；演示脚本 `e-录屏脚本.md` + `e-demo-kit/` | ✅ 已产出 |
| **作品介绍文档（PDF）** | `作品介绍文档.md` + `作品介绍文档.pdf`（项目背景/技术架构/应用场景/创新点/成效/a–e 对照） | ✅ 提交用 |
| **提交清单** | `提交清单.md`（平台三项填写指引 + a–e 对照 + 一键复现入口） | ✅ |

## 关键数字（详 `docs/data-card-v2.md`）

- 卷 sha `bca2c68c82be510f…`（92 题）· 计划 sha `7643b43a83aab4cb…`（330 检查；CRLF 固定）
- 真机证据：`runs/v2r3_20261010-healed`（552 任务，13101 文件与 input-lock 逐一一致）
- 评分：`score/judge-full-3-replay`（1674 判官记录回放；results sha `afc8b73a…`）
- 稳定性：A/A 请求级翻转 1.25%（决定性 0.12%）· 修复前极性反判 17 题（已修、已验收）
- 回归：`tools/tests`+`engine/tests` = 921 passed / 63 skipped / 0 failed

## 一页验收命令（任选环境）

```bash
python v2/02-generators/build_stock_v2.py && python v2/02-generators/assemble_v2.py
python tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json
python -m pytest tools/tests engine/tests -q
# 评分重放（零调用）→ 期望 results sha afc8b73a…、1674 cache hits
python tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir <新目录> --replay score/judge-full-3/records.jsonl --model gpt-6.1-sol \
  --outcome-version v2 --evidence-policy scoped-v2
```

## 边界声明

本提交为**探索性诊断**：人审仅单裁 48 例（AI 预筛 + 人工裁决，人机一致 91.7%、κ 0.76、
0 假通过；无第二人 κ），绝对分仍按探索性口径披露；受限维度/样本缺口
（时间纵深、敏感样本、工具面）与 v2.1 路线图见 `a-方案说明文档.md` §11 与数据卡 §5/§6。
