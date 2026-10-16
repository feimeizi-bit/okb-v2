#!/bin/sh
# 第 2 幕 · 试卷与判据防线
cd /home/okb/okb
python3 - <<'PY'
import json
rows = [json.loads(l) for l in open('v2/01-paper-v2/_paperv2.jsonl', encoding='utf-8')]
s = [x for x in rows if x['id'] == 'similar_wifi_001'][0]
print('sample:', s['id'], '| dimension:', s['dimension'], '| sessions:', len(s['script']))
for sc in s['script']:
    print('  ', sc['session'], sc.get('phase'))
PY
echo "== 判词 lint（阻断级：极性/自足性） =="
python3 tools/plan_lint.py --plan v2/01-paper-v2/paper-v2.plan.json
