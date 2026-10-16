#!/bin/sh
export PATH="$HOME/.local/bin:$PATH"
i=0
while [ "$i" -lt 8 ]; do
  i=$((i+1))
  echo "────── 系统进程监视（快照 #$i · $(date +%H:%M:%S)）──────"
  echo
  ps -eo pid,pcpu,pmem,etime,args --sort=-pcpu | grep -E "PID|openclaw|hermes|embed-server|score_four_layer|-m okb" | grep -v grep | grep -v "sleep 3600" | grep -v "sh -c openclaw" | cut -c1-106 | head -10
  echo
  sleep 1
done
echo "── 以上为长期记忆评测相关进程（ps 实时快照 · 每秒刷新）──"
sleep 600
