#!/bin/bash
# 安装并初始化 Hermes Agent（DeepSeek 驱动）
# 用法：DEEPSEEK_API_KEY=sk-xxx bash install-hermes.sh
set -e

: "${DEEPSEEK_API_KEY:?请先设置 DEEPSEEK_API_KEY 环境变量}"

echo "=== 1/3 安装 Hermes Agent（官方脚本）==="
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

HERMES="$HOME/.local/bin/hermes"

echo "=== 2/3 配置 DeepSeek 凭据与模型 ==="
"$HERMES" auth add deepseek --type api-key --api-key "$DEEPSEEK_API_KEY" --label deepseek-vm
"$HERMES" config set model.provider deepseek
"$HERMES" config set model.default deepseek/deepseek-v4-pro

echo "=== 3/3 验证 ==="
"$HERMES" -z "Reply with exactly: SUCCESS"
