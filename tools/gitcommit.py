#!/usr/bin/env python3
"""okb · 提交守门员（违例九次的制度回应）——tools/gitcommit.py

用法：python tools/gitcommit.py <message-file> [--no-push]
流程：add -A → diff 暂存 → 在 message 末尾自动追加「diff=文件清单」→ commit
     → git show --stat 回读并核对清单与事实一致 → 一致才 push；不一致**拒绝 push**
     并打印差异（把「信息超前于 diff」从人事后自查变成事实在闸门前失败）。
"""
import os
import re
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):      # GBK 控制台免崩（闸门自身不许在 push 后挂掉）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def sh(*args, chk=True):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if chk and r.returncode != 0:
        print((r.stdout or "") + (r.stderr or ""))
        sys.exit(r.returncode)
    return r.stdout or ""

def main():
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != '--no-push'):
        print(__doc__)
        return 2
    msg = open(sys.argv[1], encoding="utf-8").read().rstrip()
    print(sh("git", "add", "-A"))
    # core.quotePath=false：非 ASCII 路径不被八进制转义（annotation-κ 事故根因之一）
    names = [ln[1:].strip().strip('"') for ln in
             sh("git", "-c", "core.quotePath=false", "diff", "--cached",
                "--name-status").splitlines() if ln.strip()]
    if not names:
        print("⚠ 暂存区为空：要么先改文件，要么明确用 --allow-empty 提交（本闸门不代办空提交）")
        return 1
    msg += "\n\n[gate] diff=%d 文件：%s" % (len(names), ", ".join(names))
    # 大 diff（千级文件）的 [gate] 清单会超 Windows CreateProcess 命令行上限
    # （WinError 206）——一律走 -F 临时文件，不再拼 -m；UTF-8 无 BOM、LF。
    fd, msgfile = tempfile.mkstemp(prefix="okb-gate-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(msg + "\n")
        p = subprocess.run(["git", "commit", "-F", msgfile])
    finally:
        try:
            os.unlink(msgfile)
        except OSError:
            pass
    if p.returncode != 0:
        return p.returncode
    stat = sh("git", "show", "--stat", "--format=", "HEAD")
    fact = {ln.split("|")[0].strip() for ln in stat.splitlines() if "|" in ln}
    missing = [n for n in names if n.split("/")[-1] not in " ".join(fact)]
    # 第十次违例后升级：信息正文点名的文件名必须真实出现在本次 diff（ghost 拦截）
    import re as _re
    # 扩展名按长度降序（jsonl 先于 json），避免 ".json" 前缀吞掉 ".jsonl" 尾字母
    # 后造成假幽灵（与本闸门追加的 [gate] 清单叠加时必触发）；负向前瞻防再吃字母。
    prose = set(_re.findall(
        r"[A-Za-z0-9_./-]+\.(?:jsonl|json|yaml|yml|toml|md|py|ps1|sh)(?![A-Za-z0-9_])",
        msg))
    staged_base = {n.split("/")[-1] for n in names}
    # basename 归一化：按 / 与全/半角冒号同时拆——[gate] 行的「文件：README.md」中全角
    # 冒号在 CJK 字符类内，会把**根目录**文件名粘连成假幽灵（有斜杠的路径恰好被掩盖）
    # 通配/省略形态（含 * ? { } …）不算点名，防 batchA-*.yaml 之类被误判（3d63203 实录）
    ghost = sorted(p for p in prose
                   if "*" not in p and "?" not in p and "{" not in p
                   and _re.split(r"[/：:]", p)[-1] not in staged_base)
    if ghost:
        print("❌ 拦截：信息点名了不在本次 diff 的文件 →", ghost,
              "\n   要么补编辑，要么删声称；确属跨提交引用可在信息里加 force-msg-ghost 并解释", file=sys.stderr)
        # 越权必须行首独立标记 `[force-msg-ghost]`——正文顺带出现该词不再算数
        # （b449a5a 实证漏洞：消息写「force-msg-ghost 不适用」即被当越权放行）
        if not re.search(r"(?m)^\[force-msg-ghost\]", msg):
            return 4
    claimed = set(msg.split("[gate] diff=")[1].split("：")[1].split(", "))
    if missing or claimed != set(names):
        print("❌ 闸门拦截：commit 已建但 **不 push**。清单↔show 不符：",
              {"missing": missing, "claimed": sorted(claimed), "staged": names})
        return 3
    if '--no-push' in sys.argv:
        print("✅ 闸门通过，仅本地提交：%s" % sh("git", "rev-parse", "--short", "HEAD").strip())
    else:
        print(sh("git", "push", "origin", "main"))
        print("✅ 闸门通过并 push：%s" % sh("git", "rev-parse", "--short", "HEAD").strip())
    return 0

if __name__ == "__main__":
    sys.exit(main())
