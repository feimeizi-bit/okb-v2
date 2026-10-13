"""okb 校验器 · 负向测试运行器（V1）

断言三件事：
  ① 每条 `neg_<code>_*` 样本，必须报出对应的 <code>（且级别为 error）
  ② 每条 `warn_<code>_*` 样本，必须报出对应的 <code>，**且该样本不得有任何 error**
     —— 这一条最关键：把 warning 当 error 会让作者失去对校验器的信任
  ③ `pos_` 前缀的正向对照样本（含 `pos_control_ok`）必须 0 error **0 warning** —— 防止校验器过严

用法：python tools/tests/test_negative.py
"""
from __future__ import annotations

import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from loader import load_yaml  # noqa: E402
from validate import validate_samples  # noqa: E402

CASES = ROOT / "tools" / "tests" / "negative_cases.yaml"
ID_RE = re.compile(r"^(neg|warn)_([ew]\d{3})_")


def main() -> int:
    _, samples = load_yaml(CASES)
    report = validate_samples(samples)

    by_sample: dict[str, list] = {}
    for issue in report.issues:
        by_sample.setdefault(issue.sample_id, []).append(issue)

    failures: list[str] = []
    checked = 0

    for sample in samples:
        sid = sample.get("id", "-")
        m = ID_RE.match(sid)
        issues = by_sample.get(sid, [])
        codes = {i.code for i in issues}
        errors = [i for i in issues if i.level == "error"]
        warnings = [i for i in issues if i.level == "warning"]

        if sid.startswith("pos_"):
            checked += 1
            if errors or warnings:
                failures.append(f"正向对照 {sid} 不该有任何问题，实际：{[i.render('') for i in issues]}")
            continue

        if not m:
            failures.append(f"{sid}: id 未按 neg_<code>_ / warn_<code>_ 约定命名，无法自动断言")
            continue

        kind, code = m.group(1), m.group(2).upper()
        checked += 1

        if code not in codes:
            failures.append(f"{sid}: 期望报出 {code}，实际只报出 {sorted(codes) or '（无）'}")
            continue

        if kind == "warn" and errors:
            failures.append(f"{sid}: 只应产生 warning，却报了 error：{[i.render('') for i in errors]}")

        if kind == "neg" and not any(i.code == code and i.level == "error" for i in issues):
            failures.append(f"{sid}: {code} 的级别不是 error")

    print(f"负向测试：检查 {checked} 条样本，{len(failures)} 条不通过")
    for f in failures:
        print("  ✗", f)

    # 汇总校验器整体是否真的报了错（否则说明校验器形同虚设）
    print(f"校验器整体：{len(report.errors)} error / {len(report.warnings)} warning")
    if not report.errors:
        failures.append("整个负向样本集竟然 0 error —— 校验器没在干活")

    if failures:
        print("\n❌ 负向测试未通过")
        return 1
    print("\n✅ 负向测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
