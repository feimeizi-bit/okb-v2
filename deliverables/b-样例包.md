# 交付 b · 样例数据与样例结果（索引）

完整样例包在 **`v2/07-deliverable-b/`**（由 `tools/make_sample_pack.py` 确定性生成）：

| 文件 | 内容 |
|---|---|
| `README.md` | 包说明与隐私提示 |
| `cases/openclaw-similar_wifi_001-r2.md` | 相近区分/混淆案例：产物写错对象（含逐检查理由与引用） |
| `cases/hermes-boundary_pii_mask_001-r3.md` | 边界识别/正确拒记案例（withhold） |
| `cases/openclaw-update_delayed_001-r1.md` | 动态更新/提前使用未生效新址案例 |
| `evidence-slices.jsonl` | 三个案例引用的原始证据切片（判官引用 + 兜底证据行） |
| `manifest.json` | 包内文件哈希（同输入同字节） |

每个案例 = **任务（剧本/初始文件/正确行为）+ 真实运行证据 + 逐检查评分解释与引用 + 复现命令**。

复算：

```bash
python tools/make_sample_pack.py --report score/final-report-r12/report.json \
  --results score/judge-full-3-replay/results.json \
  --paper v2/01-paper-v2/_paperv2.jsonl --out-dir <新目录>
```

范围说明：案例取自 `runs/v2r3_20261010-healed`（552 真实任务）与
`score/judge-full-3-replay`（判官 gpt-6.1-sol 回放）；证据在引擎侧已完成 PII 占位符化，
本包仍按私有评测材料保管。
