#!/bin/bash
# 安装并初始化 OpenClaw（DeepSeek 驱动）
# 用法：DEEPSEEK_API_KEY=sk-xxx bash install-openclaw.sh
set -e

: "${DEEPSEEK_API_KEY:?请先设置 DEEPSEEK_API_KEY 环境变量}"

echo "=== 1/3 安装 OpenClaw（npmmirror 源）==="
sudo npm install -g openclaw@latest \
    --registry=https://registry.npmmirror.com \
    --allow-scripts=openclaw

echo "=== 2/3 非交互式初始化（DeepSeek）==="
openclaw onboard --non-interactive --accept-risk \
    --auth-choice deepseek-api-key \
    --deepseek-api-key "$DEEPSEEK_API_KEY" \
    --agent-name okb

echo "=== 3/3 验证 ==="
openclaw agent --local --agent okb -m "Reply with exactly: SUCCESS"
