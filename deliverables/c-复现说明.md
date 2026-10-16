# 交付 c · 可复现材料说明

> 两条复现路线：**开发机（Windows）全链复现** 与 **openKylin VM（赛题目标环境）复现**。
> 全部命令离线可跑；判官重放零调用、零联网。验收记录见 `c-openkylin-verify/receipt.md`。

## 0. 依赖与版本

- Python ≥ 3.10（开发机 3.11.9 / openKylin 3.12.2 均验证）；`PyYAML>=6.0`、`jsonschema>=4.0`（见 `pyproject.toml`）；
- 冻结版本：卷 `bca2c68c82be510f…`、计划（CRLF）`7643b43a83aab4cb…`、判官 `gpt-6.1-sol`、
  运行 `v2r3_20261010-healed`（552 任务）、评分 `score/judge-full-3-replay`。

## 1. 开发机（Windows）

```powershell
# 复算链（应输出 sha bca2c68c…、94 rewrites、plan-lint 0E）
python v2/02-generators/build_stock_v2.py
python v2/02-generators/assemble_v2.py
python v2/02-generators/build_registry.py; python v2/02-generators/qc_v2.py

# 校验与测试
python tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json    # 0 error
python tools/validate.py v2/01-paper-v2/paper-v2.yaml                 # 0 error / 61 warning
python -m pytest tools/tests engine/tests -q                          # 921 passed / 63 skipped

# 判官评分重放（零调用；<新目录> 必须不存在）
python tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl `
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed `
  --out-dir <新目录> --replay score/judge-full-3/records.jsonl --model gpt-6.1-sol `
  --outcome-version v2 --evidence-policy scoped-v2
# 期望：1674 cache hits、0 extra；results.json sha = afc8b73a26cba085…（LF 字节）

# 报告与雷达
python tools/report_four_layer.py --score-dir <重放目录> --run-dir runs/v2r3_20261010-healed `
  --paper v2/01-paper-v2/_paperv2.jsonl --plan v2/01-paper-v2/paper-v2.plan.json --out-dir <新目录>
```

## 2. openKylin VM（赛题目标环境）

```bash
# 同步（宿主执行）：powershell -ExecutionPolicy Bypass -File deploy/sync-to-vm.ps1
# VM 内：
sudo apt install -y python3-yaml python3-jsonschema     # 若缺
python3 v2/02-generators/build_stock_v2.py && python3 v2/02-generators/assemble_v2.py
python3 tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json   # 0 error
python3 tools/validate.py v2/01-paper-v2/paper-v2.yaml                # 0 error / 61 warning
# 判官评分重放（需先将宿主 runs/v2r3… 与 score/judge-full-3 送入 VM）
python3 tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir /tmp/vm-replay --replay score/judge-full-3/records.jsonl \
  --model gpt-6.1-sol --outcome-version v2 --evidence-policy scoped-v2
# 实测：与宿主逐字节一致（afc8b73a26cba085…）
```

## 3. 字节约定（重要）

- 本仓 `.gitattributes` 为 `* -text`（禁止 git 换行转换），`core.autocrlf=false`；
- 全部文本文件 = **LF**；唯一例外 `v2/01-paper-v2/paper-v2.plan.json` = **CRLF**
  （判官记录绑定其 sha `7643b43a…`，改动会导致全部记录不可回放）；
- 历史事故与修复记录见 `c-openkylin-verify/receipt.md` §5 与 `docs/data-card-v2.md`。

## 4. 复现判定标准

| 检查 | 期望 |
|---|---|
| 复算链 | paper sha `bca2c68c…`；`REQUIREMENT NORMALIZE: 94 rewrites`；plan-lint 0 error |
| 样本 lint | 0 error / 61 warning |
| 测试 | 921 passed / 63 skipped / 0 failed（VM 无 pytest 时以链+评分为准） |
| 评分重放 | 1674 cache hits、0 extra；results sha `afc8b73a…`（宿主与 openKylin 一致） |
| 报告 | `report_v2.load_bundle` 通过（complete.json 哈希自洽） |
