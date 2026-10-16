#!/bin/sh
# 第 3 幕 · 现场运行评测（两款智能体 × 2 题 × 1 轮）
# 排练：DRY=1 sh 4-scene3-run.sh（不调用 Agent）；正式录制：直接 sh
set -e
export PATH="$HOME/.local/bin:$PATH"
cd /home/okb/okb
RUN_ID="demo_$(date +%m%d_%H%M)"
if [ "${DRY:-0}" = "1" ]; then
  echo "[排练] 只验编排，不调用 Agent：run-id=$RUN_ID"
  python3 -m okb run --agents deploy/agents.yaml \
    --paper v2/01-paper-v2/_paperv2.jsonl --plan v2/01-paper-v2/paper-v2.plan.json \
    --sample-id "similar_wifi_001,update_delayed_001" --repeat 1 \
    --run-id "$RUN_ID" --out-root build/demo_live --dry-run
else
  echo "现场运行：2 题 × 2 Agent × 1 轮（run-id=$RUN_ID）"
  python3 -m okb run --agents deploy/agents.yaml \
    --paper v2/01-paper-v2/_paperv2.jsonl --plan v2/01-paper-v2/paper-v2.plan.json \
    --sample-id "similar_wifi_001,update_delayed_001" --repeat 1 \
    --run-id "$RUN_ID" --out-root build/demo_live --skip-score
  echo
  echo "== 逐轮原件（证据不是转述，是可审计的真身） =="
  ls "runs/$RUN_ID/raw" | head
  echo "run-id 记一下：$RUN_ID"
fi
