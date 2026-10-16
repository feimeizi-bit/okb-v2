# openKylin VM 复现回执（2026-10-14）

环境：openKylin-V3.0（VirtualBox headless）· Python 3.12.2 · python3-yaml 6.0.3 · python3-jsonschema 4.19.2
VM 仓库路径：`/home/okb/okb`（`deploy/sync-to-vm.ps1` 同步）· 全程离线、未调用任何 Agent 或判官。

## 1. 复算链（VM 上重建，与宿主同 sha）

```
python3 v2/02-generators/build_stock_v2.py   → WROTE stock-v2.yaml | samples: 68 | repaired: 20
python3 v2/02-generators/assemble_v2.py      → REQUIREMENT NORMALIZE: 94 rewrites | plan-lint: 0 error / 0 warning
                                               → VALIDATE_PLAN OK | samples: 92 | checks: 330 | sha: bca2c68c82be510f
python3 v2/02-generators/build_registry.py   → slots: 629 | plan_checks: 330 | anchors: 299
python3 v2/02-generators/qc_v2.py            → 完成（qc-v2-report.md 生成）
```

产物 sha（VM）：
- `v2/01-paper-v2/_paperv2.jsonl` = `bca2c68c82be510f…`（与宿主完全一致）
- `v2/01-paper-v2/paper-v2.plan.json` 由 VM 重建 = `3937fb1997e7144c…`（LF 版；宿主冻结计划为 CRLF `7643b43a…`，内容一致——见 §5 已知差异）

## 2. 校验（VM）

- `python3 tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json` → **0 error / 0 warning**
- `python3 tools/validate.py v2/01-paper-v2/paper-v2.yaml` → **0 error / 61 warning**（与宿主一致）

## 3. 判官评分重放（最强证据：跨 OS 逐字节一致）

把宿主冻结字节送入 VM（run 目录 LF + 计划 CRLF + 判官记录），执行：

```
python3 tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir /tmp/vm-replay --replay score/judge-full-3/records.jsonl \
  --model gpt-6.1-sol --outcome-version v2 --evidence-policy scoped-v2
```

结果：1674 条判官记录全部命中（0 缺失、0 多余）；`/tmp/vm-replay/results.json`
sha256 = `afc8b73a26cba085…` —— 与宿主新鲜重放逐字节一致（任务结论分布同为
pass 326 / fail 159 / unassessable 67）。

## 4. .deb 工具包（交付 d 现场证据）

```
bash deploy/build-deb.sh
→ dist/okb_0.1.0_all.deb（542,642 bytes）
  内容指纹（跨机可比）: feccf393215eec364a062d570a1546093999b915e46a1e9c162c6a69d480ce17
  成员: 259 个文件（members.txt 随包）
sudo -n dpkg -i dist/okb_0.1.0_all.deb → 安装成功（postinst 打印安装完成）
dpkg -V okb → 无输出（无完整性差异）
okb --version → okb cli 0.1 (dispatcher)
okb validate /opt/okb/samples/pilot-v0.2.yaml → 0 error / 38 warning
okb --help → 子命令清单正常（anchorcal/validate/run/score-vnext/report-vnext…）
```

.deb 已回传宿主：`deliverables/d-package/okb_0.1.0_all.deb`（+ content.sha256 + members.txt）。

## 5. 本轮演练发现并修复的真实问题（复现性）

1. **换行符跨 OS 事故**：宿主 `core.autocrlf=true` 检出曾把工作区 1.1 万个文本文件
   从 LF 转成 CRLF，导致判官记录（绑定 LF 字节）无法重放。修复：run 目录逐文件还原为
   锁定 LF 字节（13101/13101 与 input-lock 一致）；全仓统一 LF（仅
   `paper-v2.plan.json` 保持 CRLF——判官记录绑定其 sha）；新增 `.gitattributes`
   （`* -text`）+ 本地 `core.autocrlf=false` 防复发。
2. **已知差异（v2.1 修）**：生成器在 Windows 写计划为 CRLF、Linux 重建为 LF，
   内容一致但 sha 不同（`7643b43a` vs `3937fb19`）。当前提交以冻结 CRLF 计划为准
   （判官记录绑定它）；v2.1 将统一 `newline='\n'` 并重新冻结。
3. VM 上无 pytest（系统未装 python3-pytest），故测试套件以宿主全绿为准
   （914 passed / 63 skipped）；VM 侧已验证链 + 评分 + CLI + deb 全部可运行。

## 6. 结论

`c 可复现材料`：openKylin 上 **复算链可重建（paper sha 一致）、评分重放与宿主逐字节一致、
CLI/deb 可装可用** —— 通过。
