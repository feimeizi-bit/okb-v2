#!/bin/sh
# 第 3.5 幕 · 552 任务批次评分回放（零调用、可复现）
cd /home/okb/okb
rm -rf /tmp/demo-replay
python3 tools/score_four_layer.py --paper v2/01-paper-v2/_paperv2.jsonl \
  --plan v2/01-paper-v2/paper-v2.plan.json --run-dir runs/v2r3_20261010-healed \
  --out-dir /tmp/demo-replay --replay score/judge-full-3/records.jsonl \
  --model gpt-6.1-sol --outcome-version v2 --evidence-policy scoped-v2 || true
python3 - <<'PY'
import hashlib, json
s = json.load(open('/tmp/demo-replay/summary.json', encoding='utf-8'))
print('任务:', s['tasks'], '| assessed:', s['assessed'], '| unassessable:', s['unassessable'])
print('判官记录命中 cache_hits:', s['cache_hits'], '| extra:', s['extra_replay_records'])
print('results sha256:', hashlib.sha256(open('/tmp/demo-replay/results.json', 'rb').read()).hexdigest()[:16])
PY
