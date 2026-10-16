# deploy — 虚拟机部署套件

本目录包含 openKylin 评测环境的全部部署脚本与基础设施代码，目标：**照着跑一遍就能复现整个环境**。

## 目录

```
deploy/
├─ embed-server/          # OpenClaw 向量记忆所需的本地嵌入服务
│  ├─ server.py           # OpenAI 兼容的 /v1/embeddings 服务（fastembed）
│  ├─ embed-server.service# systemd 单元（开机自启）
│  └─ install.sh          # 一键安装
└─ agents/
   ├─ install-node.sh     # Node.js 26（npmmirror 二进制）
   ├─ install-openclaw.sh # OpenClaw + DeepSeek 初始化
   └─ install-hermes.sh   # Hermes Agent + DeepSeek 初始化
```

## 使用顺序

```bash
# 0. 前提：openKylin 3.0 已安装，网络可用（国内镜像已配置）
# 1. 安装 Node.js 26
bash agents/install-node.sh

# 2. 安装并初始化 OpenClaw（需要 DeepSeek key）
DEEPSEEK_API_KEY=sk-xxx bash agents/install-openclaw.sh

# 3. 安装并初始化 Hermes
DEEPSEEK_API_KEY=sk-xxx bash agents/install-hermes.sh

# 4. 部署本地嵌入服务（OpenClaw 向量记忆）
bash embed-server/install.sh
```

## 注意事项

- **API key 不写入任何文件**，脚本通过环境变量读取；已写入虚拟机配置的 key 不会被本仓库追踪
- 嵌入服务监听 `127.0.0.1:18001`（8000/8001 被系统 kytensor 占用）
- 模型与依赖均走国内镜像（npmmirror / 阿里云 PyPI / hf-mirror）
- 详细环境记录与已知问题见 [`../docs/SETUP_VM_AGENTS.md`](../docs/SETUP_VM_AGENTS.md)

## 评测引擎依赖与代码同步（A 侧）

### VM 依赖（pyyaml / jsonschema）
```bash
bash deploy/vm-deps.sh        # 安装/校验依赖，并跑一次契约校验 + 评分器单测自检
```

### 宿主机 → VM 同步（开发期；VM 内没有 git 凭据）
```powershell
powershell -ExecutionPolicy Bypass -File deploy\sync-to-vm.ps1
```

### 引擎跑批（VM 内、仓库根目录）
```bash
# 官方输入：B 的构建产物（锁 suite 版本，与评分器同源）
python3 tools/build_paper.py samples/pilot-v0.1.yaml -o build/paper.jsonl
python3 -m engine.runner --agent openclaw --paper build/paper.jsonl --run-id run_xxx

# 开发输入：直接读 suite YAML（改题即跑，不锁版本）
python3 -m engine.runner --agent hermes --suite samples/pilot-v0.1.yaml --dry-run
```

### 交付 d：okb CLI 打包（.deb 骨架，A 侧）
```bash
bash deploy/build-deb.sh             # → dist/okb_0.1.0_all.deb（dpkg-deb 直构，无网络）
sudo dpkg -i dist/okb_0.1.0_all.deb  # 依赖：python3-yaml / python3-jsonschema
okb --help                           # 九子命令直连 tools/（工作目录 /opt/okb）
```
验收记录与未竟（签名/软件源/postrm/pip 路线）见 [`../docs/A-deb-skeleton-2026-09-24.md`](../docs/A-deb-skeleton-2026-09-24.md)。
