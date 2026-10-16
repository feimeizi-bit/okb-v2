#!/bin/sh
# 预检：全绿再开录
set -e
export PATH="$HOME/.local/bin:$PATH"
cd /home/okb/okb
echo "== okb 录制预检 =="
python3 --version
echo -n "openclaw: "; openclaw --version | head -1
echo -n "hermes:   "; hermes --version | head -1
echo -n "embed-server: "; systemctl is-active embed-server
if [ -f "$HOME/.okb/relay.env" ]; then echo "relay.env: present"; else echo "relay.env: MISSING"; fi
echo "repo: $(pwd)"
df -h /home | tail -1
ls score/final-report-r12/index.html score/judge-full-3/records.jsonl >/dev/null && echo "演示件齐备（report / judge records）"
