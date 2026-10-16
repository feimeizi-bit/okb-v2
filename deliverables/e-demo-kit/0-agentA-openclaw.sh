#!/bin/sh
# 第 0 幕 · 终端 A：OpenClaw 就位
export PATH="$HOME/.local/bin:$PATH"
echo "== 被测智能体 A：OpenClaw =="
openclaw --version
echo "-- 它的工作区（文件面）--"
ls "$HOME/.openclaw/workspace" | head -12
