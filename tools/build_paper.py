"""okb 数据契约 · 构建器

人类写 YAML → 本脚本校验后转成 paper.jsonl（每行一个 JSON 对象，供引擎读取）。

设计取舍：为什么不让作者直接写 JSONL？
  ① JSONL 不能写注释、不能多行字符串，90 条样本手写几乎不可维护；
  ② 日期、tags 这类可从字段派生的信息，交给机器派生比作者手写更不容易漂移。

用法：
    python tools/build_paper.py schemas/example-samples.yaml -o build/paper.jsonl
    python tools/build_paper.py samples/*.yaml -o build/paper.jsonl --allow-warnings
规则：**有 error 就拒绝生成**（避免坏样本流进引擎）；warning 默认不阻断，可用 --strict 收紧。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from loader import derive_tags, load_any, load_any as _load  # noqa: E402,F401
from trap_expand import expand_traps  # noqa: E402
from validate import validate_samples  # noqa: E402


def build(paths: list[str], out: pathlib.Path, *, allow_warnings: bool = True,
          expand_trap_values: bool = False) -> int:
    suite_version = None
    samples: list[dict] = []

    for p in paths:
        sv, chunk = load_any(p)
        if sv and suite_version and sv != suite_version:
            print(f"ERROR [E120] 各文件的 suite_version 不一致：{suite_version} vs {sv}（{p}）", file=sys.stderr)
            return 1
        suite_version = suite_version or sv
        samples.extend(chunk)

    report = validate_samples(samples, file_suite=str(suite_version or ""))
    for i in report.issues:
        if expand_trap_values and i.code == "W206":
            continue  # 展开模式下 trap 将由本命令自动补锚点，源侧 W206 不再刷屏
        print(i.render("<build>"))
    if report.errors:
        print(f"\n拒绝生成：{len(report.errors)} 个 error。修好再跑。", file=sys.stderr)
        return 1
    if report.warnings and not allow_warnings:
        print(f"\n拒绝生成：--strict 下 {len(report.warnings)} 个 warning 也算失败。", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)

    # 先对 suite_version 做形状校验——它是文件级字段，不属任何单个样本，
    # schema 管不到它；不校验就会带着 ✅ 把 `suite_version: 0.1`（YAML float）写进每行 meta。
    if suite_version is not None and not re.match(r"^v[0-9]+\.[0-9]+$", str(suite_version)):
        print(f"ERROR [E121] suite_version 形状非法：{suite_version!r}（应为 v0.1 这种形式；YAML 里请加引号）", file=sys.stderr)
        return 1

    built = []
    expanded_total = 0
    for s in samples:
        s = dict(s)
        s["tags"] = derive_tags(s)  # tags 由机器派生，覆盖作者手写，避免漂移
        s.setdefault("meta", {})
        if suite_version is not None:
            s["meta"]["suite_version"] = str(suite_version)
        if expand_trap_values:
            added = expand_traps(s)
            if added:
                expanded_total += len(added)
                print(f"  trap 展开：{s.get('id')} +{len(added)} 锚点 → {[a['id'] for a in added]}")
        built.append(s)

    # 产物回验：上面改过 tags/meta，改动本身必须仍然合 schema（否则坏产物会流进引擎）
    prod_report = validate_samples(built, file_suite=str(suite_version or ""))
    prod_errors = [i for i in prod_report.issues if i.level == "error"]
    if prod_errors:
        for i in prod_errors:
            print(i.render("<product>"), file=sys.stderr)
        print(f"\n拒绝生成：派生 tags/meta 后的产物有 {len(prod_errors)} 个 error", file=sys.stderr)
        return 1

    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for s in built:
            fh.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")

    print(f"\n✅ 生成 {out}：{len(samples)} 条样本（suite_version={suite_version or '未声明'}）"
          + (f"；trap 自动展开 +{expanded_total} 锚点" if expand_trap_values else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YAML → paper.jsonl")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", required=True, help="输出 .jsonl 路径")
    ap.add_argument("--strict", action="store_true", help="任何 warning 也当失败")
    ap.add_argument("--expand-traps", action="store_true",
                    help="CONTRACT §5：把未被锚点覆盖的 expect.trap 展开为 soft 负证据锚点"
                         "（默认关——历史卷字节须可复现；启用会改变 paper sha，属 v1.1 流程）")
    args = ap.parse_args(argv)
    return build(args.paths, pathlib.Path(args.out), allow_warnings=not args.strict,
                 expand_trap_values=args.expand_traps)


if __name__ == "__main__":
    raise SystemExit(main())
