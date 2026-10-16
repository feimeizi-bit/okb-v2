#!/bin/sh
# 第 1 幕 · 一键 CLI 与批量对比配置
cd /home/okb/okb
echo "== 一键 CLI =="
python3 -m okb --version
python3 -m okb --help | head -16
echo
echo "== 批量对比：换被测系统只改这一个文件 =="
sed -n '13,32p' deploy/agents.yaml
