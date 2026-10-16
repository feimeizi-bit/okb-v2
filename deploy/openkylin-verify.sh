#!/usr/bin/env bash
# openKylin 安装态自检（交付 c/d：代码在 openKylin 上可直接运行 + 一键 CLI 可复现）
#
#   bash deploy/openkylin-verify.sh                 # 自检并写回执
#   OKB_ROOT=/opt/okb bash deploy/openkylin-verify.sh   # 对 .deb 安装态自检
#
# 口径：
# - 全程离线：不调用任何真实智能体、不发任何 HTTP、不产生任何费用。评分停在 prepare，
#   语义层只产出「待判请求」——这是仓内既有 prepare 语义，不是失败。
# - 用仓内 mock 合成考生（engine/adapters/mock.py，CONTRACT §14 独立子进程）验证
#   「运行→采集→评分→报告→雷达」这条链在目标 OS 上完好，不验证智能体能力。
# - 每项判定都是真实命令与真实退出码；跑不通就 FAIL 进回执，不粉饰。
# - 回执 JSON 由 python3 生成，避免 shell 拼 JSON 的转义风险。
set -uo pipefail

ORIG_PWD="$(pwd)"   # 回执默认写到调用者当前目录：脚本随后会 cd 到 OKB_ROOT，
                    # 安装态那是 root 持有的 /opt/okb，普通用户写不进去（也不该写）
OKB_ROOT="${OKB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
WORK="$(mktemp -d)"
RECEIPT="${OKB_RECEIPT:-$ORIG_PWD/openkylin-verify-receipt.json}"
PAPER="$WORK/paper-v0.2.jsonl"
RUN_ID="verify_$(date +%Y%m%d_%H%M%S)"
OUT="$WORK/out-$RUN_ID"
PLAN_REL="samples/four-layer-pilot.v1.json"
AGENTS_REL="deploy/agents.offline.yaml"
SAMPLES="retain_allergy_001,dual_order_001"
RESULTS="$WORK/results.tsv"
: > "$RESULTS"

check() { # check <name> <cmd...>  —— name 只允许 [a-z_]
  local name="$1"; shift
  local rc=0
  if "$@" > "$WORK/log.$name" 2>&1; then rc=0; else rc=$?; fi
  if [ "$rc" -eq 0 ]; then
    printf 'PASS %s\n' "$name"
    printf '%s\tpass\t0\t\n' "$name" >> "$RESULTS"
  else
    printf 'FAIL %s (rc=%s)\n' "$name" "$rc"
    sed -n '1,8p' "$WORK/log.$name" | sed 's/^/      /'
    printf '%s\tfail\t%s\t%s\n' "$name" "$rc" "$(tail -c 400 "$WORK/log.$name" | tr '\n\r' ' ')" >> "$RESULTS"
  fi
}

printf '== okb openKylin install self-check ==\n'
printf 'OKB_ROOT=%s\n' "$OKB_ROOT"
cd "$OKB_ROOT" || { printf 'FAIL cannot cd to OKB_ROOT\n'; exit 2; }
printf 'python3=%s  pwd=%s\n\n' "$(python3 -V 2>&1)" "$(pwd)"

check deps_python python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
check deps_yaml python3 -c 'import yaml, jsonschema; print(yaml.__version__, jsonschema.__version__)'
check cli_help python3 -m okb --help
check cli_run_help python3 -m okb run --help
check paper_build python3 tools/build_paper.py samples/pilot-v0.2.yaml -o "$PAPER"

# build/ 被 .gitignore 排除，试卷必须在目标机重建；重建结果必须与评分计划绑定的 sha 一致
check paper_sha_bound python3 - "$PAPER" "$PLAN_REL" <<'PY'
import hashlib, json, sys
paper, plan = sys.argv[1], sys.argv[2]
got = hashlib.sha256(open(paper, 'rb').read()).hexdigest()
want = json.load(open(plan, encoding='utf-8'))['paper_sha256']
print('paper sha :', got)
print('plan binds:', want)
sys.exit(0 if got == want else 1)
PY

check sample_validate python3 -m okb validate "$PAPER"
check main_chain python3 -m okb run --agents "$AGENTS_REL" --paper "$PAPER" \
    --plan "$PLAN_REL" --run-id "$RUN_ID" --sample-id "$SAMPLES" \
    --runs-dir "$WORK/runs" --out-root "$OUT" --verify-traces

check chain_products python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
summary = json.loads((out / 'run-summary.json').read_text(encoding='utf-8'))
stages = summary['stages']
need = [out / 'score' / 'results.json', out / 'score' / 'summary.json',
        out / 'report' / 'report.json', out / 'report' / 'index.html',
        out / 'radar.svg', out / 'radar.json']
missing = [str(p) for p in need if not p.is_file()]
if missing:
    print('MISSING PRODUCTS:', missing)
    sys.exit(1)
score = json.loads((out / 'score' / 'summary.json').read_text(encoding='utf-8'))
print('stages         :', {k: v.get('status') for k, v in stages.items()})
print('evidence_valid :', score['evidence_valid'], '/', score['tasks'])
print('evidence_tiers :', score.get('evidence_tiers'))
print('artifact reps  :', score.get('artifact_representations'))
print('judge mode     :', score.get('mode'))
sys.exit(0 if score['evidence_valid'] >= 1 and stages.get('run', {}).get('status') == 'done' else 1)
PY

check radar_missing_semantics python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
radar = json.loads((Path(sys.argv[1]) / 'radar.json').read_text(encoding='utf-8'))
print('agents      :', [a.get('name') for a in radar.get('agents', [])])
print('all_missing :', radar.get('all_missing'))
bad = []
for agent in radar.get('agents', []):
    for value in agent.get('values', []):
        status = value.get('status')
        if value.get('value') == 0 and status in ('missing', 'insufficient', None):
            bad.append([agent.get('name'), value.get('dimension'), status])
if bad:
    print('MISSING RENDERED AS ZERO:', bad)
    sys.exit(1)
PY

# ---------- 交付卷：安装态必须能跑我们**真正交付**的那份题卷（paper-r1，100 题） ----------
# 此前自检只测 20 题 pilot 卷：交付物换了题卷、换了计划，自检照样"全绿"——
# "验的不是交付的东西"这类错误只有把交付卷纳入自检才会暴露。
# 离线 mock（单 agent）跑 100 题 × 2 轮 = 200 任务，实测 33s；两个真实 Agent × 3 轮 = 600 任务
# 按比例约 100s 量级，但真机耗时**未在 openKylin 上计时**，不写成结论。
DELIVERY_PAPER="$WORK/paper-r1.jsonl"
DELIVERY_PLAN="samples/four-layer-r1.json"
RID="$RUN_ID-delivery"
DELIVERY_OUT="$WORK/delivery-$RUN_ID"

check delivery_build python3 tools/build_paper.py samples/paper-r1.yaml -o "$DELIVERY_PAPER"

check delivery_plan_bound python3 - "$DELIVERY_PAPER" "$DELIVERY_PLAN" <<'PY'
import hashlib, json, sys
got = hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest()
plan = json.load(open(sys.argv[2], encoding='utf-8'))
print('delivery paper sha:', got)
print('plan binds        :', plan['paper_sha256'])
print('plan rows / checks:', len(plan['samples']), '/', sum(len(s['checks']) for s in plan['samples']))
sys.exit(0 if got == plan['paper_sha256'] else 1)
PY

check delivery_validate python3 -m okb validate "$DELIVERY_PAPER"

check delivery_chain python3 -m okb run --agents "$AGENTS_REL" --paper "$DELIVERY_PAPER" \
    --plan "$DELIVERY_PLAN" --run-id "$RID" --runs-dir "$WORK/runs" --out-root "$DELIVERY_OUT" \
    --repeat 2 --verify-traces

check delivery_products python3 - "$DELIVERY_OUT" "$WORK/runs/$RID" <<'PY'
import json, sys
from pathlib import Path
out, run = Path(sys.argv[1]), Path(sys.argv[2])
score = json.loads((out / 'score' / 'summary.json').read_text(encoding='utf-8'))
tids = {t['task_id'] for t in json.loads((run / 'manifest.json').read_text(encoding='utf-8'))['tasks']}
print('tasks             :', score['tasks'], '| distinct task_id:', len(tids))
print('evidence_valid    :', score['evidence_valid'], '| tiers:', score.get('evidence_tiers'))
print('assessed/una      :', score['assessed'], '/', score['unassessable'])
bad = []
if score['tasks'] != 200:
    bad.append('expected 100 samples x 1 mock agent x 2 repeats = 200 tasks, got %s' % score['tasks'])
r1 = {x for x in tids if x.endswith('-r1')}
r2 = {x for x in tids if x.endswith('-r2')}
if len(r1) != 100 or len(r2) != 100:
    bad.append('repeat task naming broken: r1=%d r2=%d' % (len(r1), len(r2)))
if not score.get('evidence_tiers'):
    bad.append('scoped-v2 must stamp evidence_tiers')
for b in bad:
    print('PROBLEM:', b)
if bad:
    sys.exit(1)
print('OK: delivery paper produced 100 questions x 2 repeat attempts')
PY

# 预登记 → 跨轮汇总：身份指纹由代码派生，必须与跑批**同机同版本**，所以只能在目标机上证明
check delivery_registration python3 -m okb register-run --paper "$DELIVERY_PAPER" \
    --plan "$DELIVERY_PLAN" --agents "$AGENTS_REL" --run-id "$RID" --repeat 2 --mode prepare \
    --out "$WORK/reg.json" --emit-imports "$WORK/imports.json" \
    --runs-dir "$WORK/runs" --score-root "$WORK/scores" --allow-missing-imports

check delivery_pooling python3 - "$WORK" "$RID" "$DELIVERY_OUT" "$DELIVERY_PAPER" "$DELIVERY_PLAN" <<'PY'
import json, shutil, subprocess, sys
from pathlib import Path
work, rid, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
dst = work / 'scores' / rid                      # 导入清单按 <score-root>/<run_id>/score 约定
dst.mkdir(parents=True, exist_ok=True)
shutil.copytree(str(out / 'score'), str(dst / 'score'))
imports = json.loads((work / 'imports.json').read_text(encoding='utf-8'))
imports['batches'] = [dict(b, score_dir=str(dst / 'score'), run_dir=str(work / 'runs' / rid))
                      for b in imports['batches']]
(work / 'imports.json').write_text(json.dumps(imports, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf-8', newline='\n')
sha = (work / 'reg.json.sha256').read_text(encoding='utf-8').split()[0]
cmd = [sys.executable, '-m', 'okb', 'report-multirun', '--registration', str(work / 'reg.json'),
       '--registration-sha256', sha, '--imports', str(work / 'imports.json'),
       '--paper', sys.argv[4], '--plan', sys.argv[5], '--out-dir', str(work / 'pooled')]
p = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=900)
print((p.stdout or '').strip()[-500:])
if p.returncode != 0:
    print((p.stderr or '').strip()[-400:])
    sys.exit(1)
row = json.loads(p.stdout.strip().splitlines()[-1])
print('planned/imported/missing :', row['planned_batches'], row['imported_batches'], row['missing_batches'])
sys.exit(0 if row['imported_batches'] == row['planned_batches'] and not row['missing_batches'] else 1)
PY

# 交付包必须"装的就是要跑的那套文件"：漏打包过去只能靠人记得去 dpkg -c 比对（实测发生过
# 装了旧题卷的包）。这里直接断言关键模块/题卷/计划都在安装树里，缺一个就当场红。
check package_complete python3 - "$OKB_ROOT" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
need = ["okb/cli.py", "engine/adapter.py", "engine/runner.py", "engine/adapters/cli_template.py",
        "tools/freeze_candidates.py", "tools/freeze_gates.py", "tools/freeze_derive.py",
        "tools/freeze_lint.py", "tools/register_run.py", "tools/extend_paper_r2.py",
        "tools/score_four_layer.py", "tools/report_four_layer.py", "tools/run_suite.py",
        "tools/tests/test_chain_e2e.py", "tools/tests/test_register_run.py",
        "tools/tests/test_paper_r1_reproducible.py", "samples/paper-r1.yaml",
        "samples/four-layer-r1.json", "samples/paper-r2.yaml", "samples/four-layer-r2.json",
        # dev.2 = dev.1 的卷（逐字节不变）+ 新计划；A 用它评分，漏了就会静默退回 dev.1 判据
        "samples/four-layer-r1-dev2.json",
        "samples/paper-r2-extra.yaml", "samples/mock-policies/remember.v0.2.yaml",
        "schemas/sample.schema.json", "deploy/agents.offline.yaml", "deploy/openkylin-verify.sh",
        # 本轮 A 侧真机回执暴露的修复所引入的文件：漏一个就等于"装上的包跑不了自检"
        "tools/testtmp.py", "tools/pathutil.py", "tools/tests/paper_fixture.py",
        "tools/tests/test_no_temp_under_install_root.py",
        "tools/make_deb_appendix_facts.py", "tools/baseline_attribution.py",
        # 红线4 的校准工具：漏了就没法在目标机上复算"零死锚"。
        # 注意**不要**把 notes/ 下的凭据加进这张清单：build-deb.sh 只打包
        # okb tools engine samples schemas deploy（+ pyproject.toml / README.md），
        # 凭据类文件靠 deploy/sync-to-vm.ps1 同步到工作副本，不在 .deb 里
        # （2026-10-08 实测踩过：加了 notes/anchorcal-paper-r1.json ⇒ package_complete 必红）
        "tools/anchorcal.py"]
missing = [f for f in need if not (root / f).is_file()]
print('检查 %d 个关键文件，缺失 %d 个' % (len(need), len(missing)))
for m in missing:
    print('  MISSING', m)
sys.exit(1 if missing else 0)
PY

# 打包边界（实测）：.deb 只装 okb/ engine/ tools/ samples/ schemas/ deploy/，
# **不含 docs/ 与 notes/**——评审文档与落盘凭据随仓库走，包里只放运行所需。
# 因此下面这条清单只收录"在包内也能跑"的测试；依赖 docs/notes 的复现测试
# （test_r2_cards_doc_reproducible）刻意不列在这里，否则在 VM 上必然假红。
# 目标机上只跑不依赖 node/git 的关键链测试；全量套件（62 个）回传要求见 RUN_ON_OPENKYLIN.md
for t in test_chain_e2e test_register_run test_multirun test_scoped_evidence test_baseline_attribution test_report_attribution_section; do
  check "chain_$t" sh -c "PYTHONPATH=. python3 tools/tests/$t.py > \"$WORK/log.chain_$t\" 2>&1
    grep -qE '^OK$' \"$WORK/log.chain_$t\" || exit 1
    grep -qE '^(FAILED|ERROR)' \"$WORK/log.chain_$t\" && exit 1
    grep -qE '^Ran [1-9]' \"$WORK/log.chain_$t\" || exit 1"
done

python3 - "$RESULTS" "$RECEIPT" "$OKB_ROOT" "$RUN_ID" <<'PY'
import json, platform, sys
from pathlib import Path
results, receipt, root, run_id = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
checks = []
for line in Path(results).read_text(encoding='utf-8').splitlines():
    parts = line.split('\t')
    if len(parts) >= 3:
        checks.append(dict(name=parts[0], status=parts[1], rc=int(parts[2]), detail=parts[3] if len(parts) > 3 else ''))
release = Path('/etc/os-release')
os_name = 'unknown'
if release.is_file():
    for line in release.read_text(encoding='utf-8').splitlines():
        if line.startswith('PRETTY_NAME='):
            os_name = line.split('=', 1)[1].strip('"')
payload = dict(format='okb-openkylin-verify-v1', okb_root=root, run_id=run_id, os=os_name,
               system=platform.system(), machine=platform.machine(),
               python=sys.version.split()[0], platform=platform.platform(),
               passed=sum(1 for c in checks if c['status'] == 'pass'),
               failed=sum(1 for c in checks if c['status'] == 'fail'),
               checks=checks,
               limits=['offline self-check only: no real agent, no HTTP, no cost',
                       'prepare mode leaves semantic checks unresolved by design',
                       'evidence tiers on mock data are not agent-capability results'])
receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
print('\n== receipt: %s ==' % receipt)
print('pass=%d fail=%d' % (payload['passed'], payload['failed']))
PY

rm -rf "$WORK"
grep -q '"failed": 0' "$RECEIPT" || exit 1
