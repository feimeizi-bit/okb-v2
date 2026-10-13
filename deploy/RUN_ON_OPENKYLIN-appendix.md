# openKylin 执行单 · 附录（.deb 打包线与本机实测证据）

> 本附录只补 `RUN_ON_OPENKYLIN.md` 的交付 d（.deb 一键 CLI）部分，并列出**本机实测证据**
> 与**未取得项**。凡写成「实测」的都有原始命令与退出码；凡没跑成的写进「未取得」，不推测。
> 日期：2026-10-04。执行机：本机 WSL2 Ubuntu 24.04.3 LTS（Python 3.12.3）。

## 0. 环境与结论边界

- 验证环境是 **WSL2 Ubuntu 24.04.3**，不是 openKylin 本体。选它做代理的依据：openKylin 3.0
  是 Ubuntu 系发行版，`.deb`/`dpkg-deb`/`python3-yaml`/`python3-jsonschema` 的打包与安装语义同源；
  本机没有 openKylin VM（无 VirtualBox、无 `D:/vms`、无 `~/.ssh/okb_vm`，见 SETUP_VM_AGENTS.md
  记录的宿主是 A 的机器）。**因此「openKylin 桌面上安装成功」仍是未取得项。**
- 全程离线：不调用任何真实智能体、不发 HTTP、不产生费用。语义层停在 `prepare`（只出待判请求）。
- 自检用的是仓内 mock 合成考生（`engine/adapters/mock.py`，CONTRACT §14 独立子进程）。
  它证明**这条链在目标 OS 形态上完好**，不证明任何智能体的记忆能力。

## 1. 实测结果（本机 WSL，原始数据）

<!-- DEB-FACTS:BEGIN 由 tools/make_deb_appendix_facts.py 生成，勿手改 -->

| 项 | 实测值（由脚本量出来，不手抄） |
|---|---|
| 产物 | `dist/okb_0.1.0_all.deb` |
| 大小 | 527,940 字节 |
| sha256（容器） | `149f43bf5b7c0a349e1704d40a12ddad8485a93ac536766dc9fd8ad53087e27c` — 绑定本次构建实例（mtime/gzip 头），**跨机不可比** |
| 内容指纹（跨机可比） | `ca01386786f6826d429ed195babf8d7c8b6f764bca796713ae184ee7d056c5bc`（由 `dist/okb_0.1.0_all.deb.content.sha256` 旁证给出，同一棵树重建必同值） |
| 包成员 | 251 个文件（`(仓库根)/*` 2、`deploy/*` 25、`engine/*` 32、`okb/*` 3、`samples/*` 29、`schemas/*` 7、`tools/*` 152、`usr/*` 1） |
| 入口 | `okb --help` 子命令 **29** 个（由 `okb/cli.py` 的 COMMANDS 计数） |
| 安装态自检项数 | **24** 项（由 `deploy/openkylin-verify.sh` 的 check 调用数算出） |
| 安装态自检结果 | Ubuntu 24.04.3 LTS / Python 3.12.3 → **pass=24 fail=0**（回执 `install-state-selfcheck-2026-10-08.json`） |
| 自检覆盖交付卷 | 是（`delivery_*` 项存在） |
| 真机（openKylin）验证 | **未取得**：本表全部数值来自 Debian 系代理环境（WSL2），不能替代真机 `dpkg -i` / `lintian` / 真 Agent 跑批 |
<!-- DEB-FACTS:END -->











> 重生成：`bash deploy/build-deb.sh`（会在 `dist/` 落 `.deb` + 成员旁证 `.members.txt`）→
> `python3 tools/make_deb_appendix_facts.py`。旁证或回执与当前构建不一致时脚本**直接失败**，
> 因为它宁可空着也不写旧数字（这张表此前手抄过期过一轮：350,764 B / sha 691e61…）。





`score` 阶段状态是 `incomplete` 而非 `done`：这是仓内既有语义（prepare 永不出语义判定，
`assessed=0` 属预期），不是链路故障；链路故障会在 `run-summary.json` 里记成 `failed`。

## 2. 本轮修复的打包缺陷（都只动 `deploy/build-deb.sh`、`deploy/deb/control` 与新增文件）

| # | 缺陷 | 现象（实测） | 处置 |
|---|---|---|---|
| 1 | 包里没有 `deploy/` | 基线包 `dpkg-deb --contents \| grep -c deploy/` → **0**：装完后拿不到 `agents.yaml`，交付 d 要求的「导入不同智能体配置做批量对比」在安装态无法执行 | `build-deb.sh` 打包清单加入 `deploy`；修复后包内 `deploy/*` 25 项（含 `agents.yaml`、`agents.offline.yaml`、执行单、自检脚本） |
| 2 | `deploy/deb/postinst` 是 CRLF | 首次 `dpkg -i` 报 `unable to execute installed okb package post-installation script (/var/lib/dpkg/info/okb.postinst): No such file or directory`，包状态卡在 `iF`；`od -c` 显示 shebang 为 `#!/bin/sh \r \n`（内核按 `/bin/sh\r` 找解释器）。实测该文件含 **9 个 CR 字节** | `build-deb.sh` 在入包前对 `control`/`postinst` 一律 `tr -d '\r'`；修复后包内 postinst **CR 字节 0**，`dpkg -i` rc=0。**工作副本里 `deploy/deb/postinst` 本身仍是 CRLF**（属环境线文件，未越权改写，见 §4 风险 5） |
| 3 | 暂存目录 0700 | 基线包根条目是 `drwx------ ./`（`mktemp -d` 默认权限），安装态普通用户读不到数据面 | 构建时 `chmod 0755 "$stage"`，并统一目录 0755 / 数据 644 / 脚本 755；修复后 `dpkg-deb --contents` 非 755 目录数 **0**，`ls -ld /opt/okb` → `drwxr-xr-x` |
| 4 | 描述过时 | 基线 `control` 写「九个子命令」，且没有 `run` | 重写描述：主链路一条命令 `okb run --agents deploy/agents.yaml`，并列出全部子命令；补「Version 取值来源＝`pyproject.toml [project].version`」 |
| 5 | 自检回执默认写进安装目录 | 脚本会 `cd` 到 `OKB_ROOT`，`$PWD` 变成 root 持有的 `/opt/okb`，普通用户跑就写不进回执 | 先记录 `ORIG_PWD`，回执默认落调用者目录（实测：`/root/okbfinal/openkylin-verify-receipt.json`，`/opt/okb` 下无泄漏） |
| 6 | 重建不幂等 | `dpkg-deb` 不覆盖既有 `dist/*.deb`，第二次构建直接失败 | `build-deb.sh` 构建前 `rm -f "$deb"`；实测重复构建成功且报告 size/sha256 |

## 3. A 在 openKylin VM 上的照抄命令（构建 → 安装 → 自检）

```bash
# 0) 依赖（openKylin 源内包名与版本需现场确认，见 §4 风险 2）
python3 -c "import yaml, jsonschema; print(yaml.__version__, jsonschema.__version__)"

# 1) 构建 .deb（在仓库工作副本内；产物 dist/okb_0.1.0_all.deb）
bash deploy/build-deb.sh
sha256sum dist/okb_0.1.0_all.deb        # 回传这个指纹

# 2) 安装
sudo dpkg -i dist/okb_0.1.0_all.deb
dpkg -l okb | tail -1                   # 期望 ii
command -v okb                          # 期望 /usr/bin/okb

# 3) 安装态自检（离线，零费用；回执写当前目录）
mkdir -p ~/okb-verify && cd ~/okb-verify
OKB_ROOT=/opt/okb bash /opt/okb/deploy/openkylin-verify.sh
cat openkylin-verify-receipt.json       # 期望 failed=0；passed 应等于上方 DEB-FACTS 表的自检项数

# 4) 回传：openkylin-verify-receipt.json + dist/*.deb 的 sha256 + `dpkg -l okb` 原文
```

真实智能体对比跑批仍按 `RUN_ON_OPENKYLIN.md` §2–§3（把 `deploy/agents.offline.yaml`
换成 `deploy/agents.yaml`）。

## 4. 风险清单（判定依据，不是猜测）

1. **无 `LICENSE`/`debian/copyright`**：实测 `dpkg -i` **没有**因此报警（dpkg 不检查许可），
   但 openKylin 软件源收录与 `lintian` 一致性检查通常要求许可与 `debian/copyright`。
   本机未装 `lintian`，**这条判断未经 lintian 实测**（见 §5）。
   按既有约定：仓内没有已审定的权利人许可，**不凭空生成 LICENSE 文件**——属发布阻断项，
   需人工确认许可后才能补。
2. **依赖包名/版本未在 openKylin 验证**：`control` 的 `Depends` 写的是 `python3-yaml` /
   `python3-jsonschema`。本机 WSL 里 python3 能 import 成功（系统包已在），但 openKylin 3.0
   源里是否有同名包、版本是否 ≥ `PyYAML 6 / jsonschema 4`，**只在 A 的盘点里记录过
   PyYAML 6.0.3 / jsonschema 4.19.2 的「VM 已有值」，未验证 apt 包名**。
3. **WSL2 ≠ openKylin 桌面**：内核是 `6.6.87.2-microsoft-standard-WSL2`，无 UKUI、无 systemd
   桌面会话；`deploy/embed-server/embed-server.service`（systemd 单元）与 OpenClaw 向量记忆链路
   本次**完全未触及**。
4. **自检不代表智能体能力**：`main_chain` 用 mock 合成考生，`evidence_tiers` 全是
   `T1_declared_snapshot`（声明快照档），既不是真实观测回执也不是带外验证的记忆存储证明。
5. **工作副本仍有 CRLF 文件**：`deploy/deb/postinst`（9 CR 字节）。本轮只在入包时净化，
   未改源文件；若有人绕过 `build-deb.sh` 直接拷 `postinst` 进包，同样的安装失败会复现。
6. **Version 单一来源**：包版本取自 `pyproject.toml`（当前 0.1.0），与仓内文档里的
   「benchmark-spec v1.1 / paper v0.2」等编号无自动关联；升版本要同时改两处。
7. **Architecture: all 的前提**：工具链纯 Python + 数据面，无原生扩展；若日后把
   `rpds-py` 之类带原生二进制的依赖打进包，`all` 就不再成立。

## 5. 未取得（明确没跑成的）

- openKylin 3.0 真机上 `dpkg -i` 安装与 `okb` 运行（本机无 VM，无任何 openKylin 执行记录）。
- `lintian` / openKylin 软件源收录检查（未安装该工具，未提交源）。
- 真实智能体（OpenClaw / Hermes / KylinBot / kylin-agent）在安装态下的跑批——本附录只验 mock 链路。
- 第三适配器：`engine/runner.py --agent` 仍只认 `openclaw|hermes|mock`，任务书点名的
  KylinBot / kylin-agent **尚无 adapter**。
- `deploy/embed-server/` 服务链路（systemd 单元、127.0.0.1:18001、usage 写死 0 的问题）未验证。
- 语义判官在安装态下的 `--live/--replay` 评分（自检刻意停在 prepare，零 HTTP）。
- `.deb` 的签名、`Packages` 索引与源分发流程。

## 6. 100 题卷的构成与已知边界（2026-10-04 复算）

- `samples/paper-r1.yaml` = 75 张编译卡 + 20 条 pilot-v0.2 + 5 条 batchA（文本级并入）；
  `samples/four-layer-r1.json` = 100 行 / 295 项强制检查；`build/paper-r1.jsonl` sha256 前缀
  `0da837550437684b`，Windows 与 Linux clone 两端逐字节一致（已实测）。
- 卡片侧新增两类**不编造判据**的承载方式：结构锚点（`key_regex=".*"`=记忆面非空、
  `regex=".+"`=探测轮必有回复，reason 里标 STRUCTURAL）与无损搬运（卡片会话级 `purpose`
  折进 `note` 的 `[card-meta …]`，台账见矩阵 `relocated_session_metadata`）。
- 仍挡在门外的 17 张卡：卡片 phase=`update` 位于 disturb 之后，试卷三相枚举 + E103 单调性
  容不下它（映射成 write 触发 E103，映射成 disturb 与语义相反）⇒ 拒收并记入
  `gaps.paper_phase_enum_limitation`；`dynamic_update` 维判据因此全部来自 pilot/batchA。
- 5 张卡因 24h/5min 真实等待与进程重启要求默认被拒（本卷用 `--allow-restart`
  `--include-long-wait` 放开了 9 张里可放的部分）；`memory_before` 语义 scope 计数为 0，
  是真实缺口不是漏统计。
