# 交付 d · 工具化封装（一键 CLI + .deb）

## 1. 一键 CLI（okb）

`okb` 是薄分发层（`okb/cli.py`）：不做业务逻辑，统一把子命令转发到 `tools/` 对应模块，
工作目录固定为安装根（`/opt/okb` 或仓库根），保证 `samples/ schemas/ deploy/` 相对可达。

```bash
okb --version            # okb cli 0.1 (dispatcher)
okb --help               # 子命令清单
okb validate <卷>        # 校验（0 error / N warning）
okb run --agents deploy/agents.yaml --paper <卷> ...   # 批量跑批（采集→评分→统计）
okb score-vnext …        # 四层评分（prepare/replay/live）
okb report-vnext …       # 诊断报告；report-multirun 跨轮汇总
okb anchorcal … / plan_lint / item_stats / metric_bounds / judge_aa  # 校准与分析
```

**多智能体批量对比**：`deploy/agents.yaml`（真实双 Agent）、`agents.offline.yaml`（离线演示）、
`agents.cli-fake.yaml`（接入模板示例）。接入新 Agent 只改此文件（adapter=cli + 命令模板 + 字段抽取路径），
引擎/采集/评分零改动。

## 2. .deb 软件包（openKylin 实测）

```bash
bash deploy/build-deb.sh        # → dist/okb_0.1.0_all.deb（幂等、确定性时间戳）
sudo dpkg -i dist/okb_0.1.0_all.deb
okb --version && okb validate /opt/okb/samples/pilot-v0.2.yaml
```

- 构成：`/opt/okb` 全仓数据面（okb/tools/engine/samples/schemas/deploy + pyproject/README）；
  `/usr/bin/okb` shim = `cd /opt/okb && exec python3 -m okb "$@"`；依赖 `python3(-yaml/-jsonschema)`；
- 现场实测（openKylin VM，2026-10-14）：安装成功、`dpkg -V okb` 无差异、CLI 正常；
- 产物体：`d-package/okb_0.1.0_all.deb`（542,642 bytes）
  + `okb_0.1.0_all.members.txt`（259 文件清单）
  + `okb_0.1.0_all.content.sha256` = `feccf393215eec364a062d570a1546093999b915e46a1e9c162c6a69d480ce17`（内容指纹，跨机可比）。
    复算口径：构建面 `zh_CN.UTF-8` locale 下 `find | sort`；跨机复算需同 locale（如 `LC_ALL=zh_CN.UTF-8`），
    已在 openKylin VM 上原生复算一致（2026-10-14）。

## 3. 容器 SHA 与内容指纹

- 容器 sha256 绑定构建实例（mtime/压缩头），换机重建会不同；
- **内容指纹**（对包内每个文件"路径+内容哈希"再摘要）跨机可比，文档引用以它为准
  （同 locale 排序口径；复算方法见 §2 注）；
- 验收回执：`c-openkylin-verify/receipt.md` §4。
