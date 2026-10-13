#!/bin/bash
# 安装本地嵌入服务（fastembed + BAAI/bge-small-zh-v1.5）
# 依赖：uv（Hermes 安装器自带；若没有请先安装 uv）
set -e
cd "$(dirname "$0")"

UV_BIN="$HOME/.hermes/bin/uv"
if [ ! -x "$UV_BIN" ]; then
    UV_BIN="$(command -v uv || true)"
fi
if [ -z "$UV_BIN" ]; then
    echo "错误：未找到 uv，请先安装（curl -LsSf https://astral.sh/uv/install.sh | sh）" >&2
    exit 1
fi

echo "=== 1/4 创建虚拟环境并安装依赖 ==="
mkdir -p "$HOME/embed-server"
"$UV_BIN" venv "$HOME/embed-server/venv" --python 3.12
UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
    "$UV_BIN" pip install --python "$HOME/embed-server/venv/bin/python" \
    fastembed fastapi uvicorn

echo "=== 2/4 复制服务代码 ==="
cp server.py "$HOME/embed-server/server.py"

echo "=== 3/4 安装 systemd 服务 ==="
sudo cp embed-server.service /etc/systemd/system/embed-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now embed-server

echo "=== 4/4 验证 ==="
sleep 8
systemctl is-active embed-server
curl -s http://127.0.0.1:18001/health
echo ""
echo "完成。OpenClaw 侧配置："
echo "  openclaw config set memory.search.provider openai-compatible"
echo "  openclaw config set memory.search.remote.baseUrl http://127.0.0.1:18001/v1"
echo "  openclaw config set memory.search.model BAAI/bge-small-zh-v1.5"
