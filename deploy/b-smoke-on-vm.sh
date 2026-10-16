#!/usr/bin/env bash
# okb · B 工具链 openKylin 冒烟包（A 在 VM 一条命令跑，产出=交付 c「可在 openKylin 运行」的证据件）
# 用法（VM 仓库根）：bash deploy/b-smoke-on-vm.sh 2>&1 | tee notes/b-smoke-$(date +%F).md
set -u
cd "$(cd "$(dirname "$0")" && pwd)/.."  # G7 修：VM 副本非 git 仓库，rev-parse 必败（改按脚本自身定位仓库根）
PY=${PYTHON:-python3}
echo "# okb B 工具链 openKylin 冒烟（$(date -Iseconds)）"
echo; echo "## 环境"; $PY -VV; cat /etc/os-release 2>/dev/null | head -2
fail=0
run(){ desc="$1"; shift; if "$@" >/dev/null 2>&1; then echo "PASS  $desc"; else echo "FAIL  $desc (rc=$?)"; fail=1; fi }
echo; echo "## 测试面（十二套）"
for f in tools/tests/test_*.py; do run "$f" $PY "$f"; done
echo; echo "## 链路面"
run "build paper"      $PY tools/build_paper.py samples/pilot-v0.2.yaml -o build/paper-v0.2.jsonl
run "validate v0.2"    $PY tools/validate.py build/paper-v0.2.jsonl
run "c2 matrix"        $PY tools/c2_run.py --runs-dir runs --suite v0.2 --skip-ablate
run "snapshot selfchk" $PY tools/tests/test_snapshot_selfcheck.py
echo; echo "## 判定"; [ $fail -eq 0 ] && echo "ALL PASS（把本文件提交回仓库 notes/b-smoke-*.md 即成证据件）" || echo "有 FAIL——贴 stderr 给 B，勿即兴修判据"
exit $fail
