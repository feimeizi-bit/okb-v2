#!/bin/sh
# 第 0 幕 · 终端 B：Hermes 就位
export PATH="$HOME/.local/bin:$PATH"
echo "== 被测智能体 B：Hermes =="
hermes --version
echo "-- 它的记忆面 --"
ls "$HOME/.hermes/memories"
