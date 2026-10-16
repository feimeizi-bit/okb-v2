#!/bin/bash
# 安装 Node.js（npmmirror 二进制，满足 OpenClaw 要求 >=24.16 或 >=26.1）
# 用法：bash install-node.sh [版本号，默认 v26.9.0]
set -e

NODE_VER="${1:-v26.9.0}"
FN="node-${NODE_VER}-linux-x64.tar.xz"

echo "=== 下载 Node.js ${NODE_VER}（npmmirror）==="
cd /tmp
curl -sL -o node.tar.xz "https://registry.npmmirror.com/-/binary/node/${NODE_VER}/${FN}"

echo "=== 解压到 /usr/local ==="
sudo tar -xJf node.tar.xz -C /usr/local --strip-components=1
hash -r

echo "=== 版本验证 ==="
node -v
npm -v
