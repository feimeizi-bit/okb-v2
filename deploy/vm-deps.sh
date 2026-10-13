#!/bin/bash
# VM 侧依赖安装/自检（在 openKylin VM 内、仓库根目录运行）
# 用法：bash deploy/vm-deps.sh
set -e
echo "== Python 依赖 =="
if ! python3 -c "import yaml" 2>/dev/null; then
  sudo apt-get install -y python3-yaml
fi
if ! python3 -c "import jsonschema" 2>/dev/null; then
  sudo apt-get install -y python3-jsonschema
fi
python3 - <<'PY'
import sys
import yaml
import jsonschema
print(f"python {sys.version.split()[0]} · pyyaml {yaml.__version__} · jsonschema {jsonschema.__version__}")
PY
echo
echo "== 自检：数据契约校验（期望 0 error；warning 数随试卷规模变化，实测 pilot 38 / paper-r1 69，不写死期望值）=="
cd "$(dirname "$0")/.."
python3 tools/validate.py schemas/example-samples.yaml samples/pilot-v0.1.yaml 2>&1 | tail -2
echo
echo "== 自检：评分器单测（期望全过）=="
python3 tools/tests/test_negative.py 2>&1 | tail -1
