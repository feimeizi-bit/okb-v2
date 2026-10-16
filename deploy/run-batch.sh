#!/usr/bin/env bash
# okb 长跑包装（VM 内用）：tmux（无则 nohup）后台跑批，掉线不中断
# 用法：bash deploy/run-batch.sh <run_id> [engine.runner 参数...]
# 例：  bash deploy/run-batch.sh run_20260923_001 --agent openclaw --paper build/paper-v0.2.jsonl
# 续跑：bash deploy/run-batch.sh run_20260923_001 --resume --agent openclaw --paper build/paper-v0.2.jsonl
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "用法：bash deploy/run-batch.sh <run_id> [engine.runner 参数...]" >&2
  exit 2
fi
RUN_ID="$1"; shift
# 同一逻辑 run_id 多目录并行时（--run-dir），用目录名做 tmux/console/exit 的键，避免互相覆盖
KEY="$RUN_ID"
prev=""
for arg in "$@"; do
  if [ "$prev" = "--run-dir" ]; then KEY="$(basename "$arg")"; fi
  prev="$arg"
done
mkdir -p runs
LAUNCH="runs/${KEY}.cmd.sh"
CONSOLE="runs/${KEY}.console.log"
EXIT_FILE="runs/${KEY}.exit"
rm -f "$EXIT_FILE"   # 上次同 KEY 的残留退出码会造成误判

{
  echo '#!/usr/bin/env bash'
  echo 'set -uo pipefail'
  echo "cd $(printf '%q' "$(pwd)")"
  printf 'python3 -m engine.runner --run-id %q' "$RUN_ID"
  for arg in "$@"; do printf ' %q' "$arg"; done
  echo " 2>&1 | tee -a $(printf '%q' "$CONSOLE")"
  echo "echo \"\${PIPESTATUS[0]}\" > $(printf '%q' "$EXIT_FILE")"
} > "$LAUNCH"
chmod +x "$LAUNCH"

if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t "$KEY" 2>/dev/null || true
  tmux new-session -d -s "$KEY" "bash $(printf '%q' "$LAUNCH")"
  echo "已启动 tmux 会话：$KEY"
  echo "  查看：tmux attach -t $KEY（detach：Ctrl-b d）"
else
  nohup bash "$LAUNCH" >/dev/null 2>&1 &
  echo "已后台启动（nohup）：pid=$!"
fi
echo "  console：$CONSOLE"
echo "  退出码：$EXIT_FILE（0 ok / 1 有 invalid / 3 守卫停机 / 130 信号）"
echo "  运行日志：runs/<run-dir 或 run-id>/run.log"
