#!/bin/sh
cd /home/okb/okb
echo "等待运行启动…"
while [ ! -f /tmp/scene3b_rid ]; do sleep 1; done
RID=$(cat /tmp/scene3b_rid)
LOG="runs/$RID/run.log"
i=0
while [ ! -f /tmp/scene3b_done ]; do
  i=$((i+1))
  echo "───── 运行监视 #$i · $(date +%H:%M:%S) ─────"
  echo "run-id: $RID"
  if [ -f "$LOG" ]; then
    cur=$(tail -80 "$LOG" | grep '"task_start"' | tail -1 | sed 's/.*"task_id": "\([^"]*\)".*/\1/')
    [ -n "$cur" ] && echo "当前任务: $cur"
    echo "最近事件："
    tail -3 "$LOG" | cut -c1-108 | sed 's/^/  /'
  else
    echo "  （清场与布置中…）"
  fi
  n=$(ls "runs/$RID/raw" 2>/dev/null | wc -l)
  w=$(ls "runs/$RID/work" 2>/dev/null | wc -l)
  echo "raw/: ${n} 个目录 · work/: ${w} 个"
  echo "活跃进程（CPU% · 时长 · 进程）："
  ps -eo pcpu,etime,comm --sort=-pcpu 2>/dev/null | grep -E "openclaw|hermes|node" | grep -v grep | head -3 | sed 's/^/  /'
  echo
  sleep 3
done
echo "───── 运行完成 · $(date +%H:%M:%S) ─────"
sleep 30
