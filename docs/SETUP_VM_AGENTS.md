# 环境搭建记录：openKylin 虚拟机 + 两款 Agent

> 负责人：A（评测框架线）　日期：2026-09-20　状态：已完成
> 用途：赛题交付物"可复现材料"的基础文档；后续证据采集与批量评测均基于此环境

## 0. 总览

| 项目 | 结果 |
|---|---|
| 操作系统 | openKylin 3.0 (huanghe)，内核 7.0.0-2-generic |
| Agent 1 | OpenClaw 2026.9.5 ✅ 对话验证通过 |
| Agent 2 | Hermes Agent v0.21.3 ✅ 对话验证通过 |
| 模型 | DeepSeek `deepseek-v4-pro`（API） |
| OpenClaw 向量记忆 | ✅ 已启用（本地嵌入服务） |
| 访问方式 | `ssh -p 2222 okb@127.0.0.1`（密钥免密） |

## 1. 宿主与虚拟机配置

- 宿主：Windows 11 Pro 24H2，i5-11400H，16GB 内存
- 虚拟化软件：VirtualBox 7.2.18，安装于 `D:\tools\VirtualBox`
- 虚拟机：`openKylin-V3.0`，文件位于 `D:\vms\openKylin-V3.0`
- VM 规格：4 vCPU / 6GB 内存 / 64GB 动态 VDI / VMSVGA 显卡 256MB / USB 平板鼠标 / NAT + 端口转发 `2222→22`
- 镜像：`openKylin-Desktop-V3.0-20260905-x86_64.iso`
  - 来源：官方 CDN `https://cdimage.openkylin.top/3.0/`
  - 大小校验：8,089,612,288 字节（已核对）

## 2. openKylin 安装要点

1. ISO 挂载到 VM 光驱，从光驱启动
2. 安装器选择：**全盘安装**（自动分区，格式化系统盘+数据盘）
3. 账户：`okb` / 密码见本地私密记录（**不入库**；该 VM 仅供本地开发，未暴露公网）
4. 安装完成后**重启前必须弹出 ISO**，否则会再次进入安装器

### 已知问题：图形界面卡死

openKylin 3.0 的 live/安装环境在 VirtualBox 下可能卡死（UKUI 合成器挂起，症状：屏幕静止、CPU 归零、Guest Additions 心跳仍在）。缓解方法：安装期间由宿主机每 15 秒注入一次无害按键（Scroll Lock）保持系统活跃。本次安装即用此方法完成。

## 3. 基础软件

| 组件 | 版本 | 安装方式 |
|---|---|---|
| Node.js | 26.9.0 | npmmirror 二进制包解压至 `/usr/local` |
| npm | 11.19.1 | 随 Node.js |
| Python | 3.12.2 | 系统自带 |
| uv | 0.12.17 | Hermes 安装器自动安装（`~/.hermes/bin/uv`） |
| git | 2.43.0 | 系统自带 |

> 注意：openKylin 源内 nodejs 为 22.x，不满足 OpenClaw 要求（≥24.16 或 ≥26.1），故使用官方二进制包：
> `https://registry.npmmirror.com/-/binary/node/v26.9.0/node-v26.9.0-linux-x64.tar.xz`

## 4. OpenClaw 安装与配置

```bash
# 安装（npmmirror 源）
sudo npm install -g openclaw@latest \
  --registry=https://registry.npmmirror.com \
  --allow-scripts=openclaw

# 非交互式初始化（DeepSeek）
openclaw onboard --non-interactive --accept-risk \
  --auth-choice deepseek-api-key \
  --deepseek-api-key '<DEEPSEEK_API_KEY>' \
  --agent-name okb
```

- 配置：`~/.openclaw/openclaw.json`
- 工作区：`~/.openclaw/workspace`（含 MEMORY.md、USER.md、memory/ 等记忆文件）
- 默认模型：`deepseek/deepseek-v4-pro`
- 验证：
  ```bash
  openclaw agent --local --agent okb -m "Reply with exactly: SUCCESS"
  ```

## 5. OpenClaw 向量记忆（本地嵌入服务）

**问题**：OpenClaw 默认用 OpenAI embeddings；无 key 时向量检索降级为纯关键词（FTS），影响记忆能力评测。

**方案**：自建 OpenAI 兼容嵌入服务（fastembed + `BAAI/bge-small-zh-v1.5`，512 维，中文优化）

| 项目 | 值 |
|---|---|
| 服务代码 | `~/embed-server/server.py` |
| 虚拟环境 | `~/embed-server/venv`（uv 创建） |
| 依赖 | fastembed、fastapi、uvicorn（阿里云 PyPI 镜像安装） |
| 监听 | `127.0.0.1:18001`（8000/8001 被系统 kytensor 占用） |
| 模型源 | hf-mirror.com（`HF_ENDPOINT` 环境变量） |
| 开机自启 | systemd 服务 `embed-server.service` |

OpenClaw 侧配置：

```bash
openclaw config set memory.search.provider openai-compatible
openclaw config set memory.search.remote.baseUrl http://127.0.0.1:18001/v1
openclaw config set memory.search.model BAAI/bge-small-zh-v1.5
openclaw config set memory.search.remote.apiKey dummy-local
openclaw memory index
```

**验证**（语义命中，非关键词匹配）：
写入记忆"用户喜欢喝美式咖啡，不喜欢加糖"，查询"主人平时爱喝什么饮料？"→ 命中，相似度 0.353。

## 6. Hermes Agent 安装与配置

```bash
# 安装（官方脚本）
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# 凭据与模型
hermes auth add deepseek --type api-key --api-key '<DEEPSEEK_API_KEY>' --label deepseek-vm
hermes config set model.provider deepseek
hermes config set model.default deepseek/deepseek-v4-pro

# 验证
hermes -z "Reply with exactly: SUCCESS"
```

- 配置：`~/.hermes/config.yaml`
- CLI：`~/.local/bin/hermes`
- 安装目录：`~/.hermes/hermes-agent`

## 7. 已知问题与待办

| 问题 | 状态 | 说明 |
|---|---|---|
| Playwright Chromium（Hermes 浏览器工具） | 未安装 | openKylin 不在 Playwright 支持列表，需手动装系统依赖 |
| cua-driver（Hermes Computer Use） | 未安装 | GitHub 下载超时；属可选项 |
| OpenClaw 托管 llama.cpp 插件 | 未启用 | 官方仅支持交互式安装；改用自建嵌入服务替代 |
| 证据采集脱敏 | 待办 | 采集原始请求时需掩码 `Authorization` 头（接口单 I-12） |
| 3.0 live 环境卡死 | 已知 | 见第 2 节缓解方法 |

## 8. 快速复现清单

1. VirtualBox 建 VM：4C / 6GB / 64GB，NAT + 2222→22 端口转发
2. 官方 ISO 安装 openKylin 3.0，账户 `okb`
3. 装 Node 26.9.0（npmmirror 二进制）
4. 装 OpenClaw + onboard（DeepSeek）
5. 装 Hermes + auth（DeepSeek）
6. 部署 embed-server（fastembed + systemd）
7. 依次执行第 4/5/5 节的验证命令

> 安全说明：本文档不包含 API key；key 仅存于虚拟机内配置文件（`~/.openclaw/openclaw.json`、Hermes 凭据池），且评测证据落盘前必须脱敏。该 VM 仅本地开发使用，未暴露公网；系统密码见本地私密记录。密码未更换：该仓库为私有、VM 仅本地开发且未暴露公网，已评估并接受「历史提交中存在该密码」的风险。
