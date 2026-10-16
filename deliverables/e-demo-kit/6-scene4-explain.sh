#!/bin/sh
# 第 4 幕 · 判定可解释（逐检查理由与引用）
cd /home/okb/okb
python3 - <<'PY'
import json
r = json.load(open('score/final-report-r12/report.json', encoding='utf-8'))
t = [x for x in r['tasks'] if x['task_id'] == 'openclaw-similar_wifi_001-r2'][0]
print('任务:', t['task_id'], '| 结论:', t['task_outcome'], t['error_class'])
for c in t['checks']:
    print(' -', c['id'], '->', c['result'], '|', (c.get('rationale') or '')[:70])
PY
