#!/usr/bin/env python3
"""重写 RUN_ON_OPENKYLIN-appendix.md 里的"包事实"表：数值一律**量出来**，不手抄。

为什么：那张表曾长期手写，本轮审计时发现它整片停在一次旧构建上
（350,764 字节 / sha 691e61… / 215 个成员 / 20 个子命令），而真实包早已是另一套。
手抄的数字没有"过期检测"，只有重算才有。

能本机算出来的：大小、sha256、包成员（解析 ar + data.tar.xz）、子命令条数、自检项数、
以及安装态回执（OS/Python/pass/fail）——回执是 WSL 实跑落盘的 JSON，读它而不是回忆。
算不出的一律不写：真机 `dpkg -i` 结果、lintian、真 Agent 跑批读数，这些标为"未取得"。
"""
from __future__ import annotations

import hashlib
import io
import json
import lzma
import pathlib
import re
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "deploy/RUN_ON_OPENKYLIN-appendix.md"
BEGIN = "<!-- DEB-FACTS:BEGIN 由 tools/make_deb_appendix_facts.py 生成，勿手改 -->"
END = "<!-- DEB-FACTS:END -->"


def deb_members(path: pathlib.Path) -> list[str]:
    """成员清单优先读构建现场落下的旁证 `<pkg>.members.txt`。

    为什么不在这里解 zstd：Ubuntu 24.04 起 dpkg 默认 `data.tar.zst`，而 Python 3.12 无 zstd
    解码器、目标机也不一定有 CLI zstd。**构建现场**才有 dpkg-deb，所以由 build-deb.sh 落旁证；
    旁证缺失时明确报错，绝不沿用上一次的数字（那张手抄表就是这么过期的）。
    """
    # 与 build-deb.sh 的 ${deb%.deb}.members.txt 一致：去掉 .deb 后缀再拼
    stem = path.name[:-4] if path.name.endswith(".deb") else path.name
    side = path.parent / (stem + ".members.txt")
    if side.is_file():
        return [l.strip() for l in side.read_text(encoding="utf-8").splitlines() if l.strip()]
    raise SystemExit("缺少成员旁证 %s：请在有 dpkg 的环境重跑 deploy/build-deb.sh"
                     "（事实表宁缺不旧）" % side.name)


def _deb_members_ar(path: pathlib.Path) -> list[str]:
    """.deb = ar 归档：手工解析（仅作 xz 时代的回退实现）。"""
    raw = path.read_bytes()
    if not raw.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive: " + str(path))
    pos = 8
    out = []
    while pos + 60 <= len(raw):
        hdr = raw[pos:pos + 60].decode("latin-1")
        name = hdr[:16].strip().rstrip("/")
        size = int(hdr[48:58].strip())
        body = raw[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
        if name.startswith("data.tar"):
            tf = tarfile.open(fileobj=io.BytesIO(lzma.decompress(body) if name.endswith(".xz") else body))
            out = sorted(m.name for m in tf.getmembers() if m.isfile())
    return out


def verify_check_count() -> int:
    v = (ROOT / "deploy/openkylin-verify.sh").read_text(encoding="utf-8")
    static = len([l for l in v.splitlines() if l.startswith("check ")])
    loop = re.search(r"for t in ([a-z_0-9 ]+); do", v)
    return static + (len(loop.group(1).split()) if loop else 0)


def subcommand_count() -> int:
    cli = (ROOT / "okb/cli.py").read_text(encoding="utf-8")
    block = cli[cli.index("COMMANDS = {"):cli.index("\n}", cli.index("COMMANDS = {"))]
    return len(re.findall(r'^\s*"[^"]+":', block, re.M))


def latest_receipt() -> dict | None:
    cands = sorted((ROOT / "notes").glob("install-state-selfcheck-*.json"))
    if not cands:
        return None
    return json.loads(cands[-1].read_text(encoding="utf-8")), cands[-1].name


def main(argv=None) -> int:
    out = argv and argv[0] or None
    debs = sorted((ROOT / "dist").glob("okb_*.deb"))
    if not debs:
        raise SystemExit("dist/ 下没有 .deb：先跑 deploy/build-deb.sh")
    deb = debs[-1]
    data = deb.read_bytes()
    size, sha = len(data), hashlib.sha256(data).hexdigest()
    members = deb_members(deb)
    side_content = deb.parent / (deb.name[:-4] + ".content.sha256")
    content_sha = side_content.read_text(encoding="utf-8").strip() if side_content.is_file() else None
    tops = {}
    for m in members:
        rel = re.sub(r"^\./opt/okb/", "", m)          # 先剥安装前缀
        rel = re.sub(r"^\./", "", rel)                 # 再剥 ./，否则 ./usr/bin/okb 会掉进 "." 桶
        head = rel.split("/", 1)[0] if "/" in rel else "(仓库根)"
        tops.setdefault(head, []).append(rel)
    rec = latest_receipt()
    if rec:
        r = rec[0]
        want = verify_check_count()
        if r["passed"] + r["failed"] != want:
            raise SystemExit(
                "回执里的自检项数(%d=%d+%d)与当前脚本(%d 项)不一致：%s 是旧回执。"
                "请在目标/代理环境重跑 openkylin-verify.sh 并把回执复制到 notes/ —— 事实表宁缺不旧"
                % (r["passed"] + r["failed"], r["passed"], r["failed"], want, rec[1]))
    lines = [BEGIN, "",
             "| 项 | 实测值（由脚本量出来，不手抄） |",
             "|---|---|",
             "| 产物 | `%s` |" % deb.relative_to(ROOT).as_posix(),
             "| 大小 | %s 字节 |" % f"{size:,}",
             "| sha256（容器） | `%s` — 绑定本次构建实例（mtime/gzip 头），**跨机不可比** |" % sha,
             "| 内容指纹（跨机可比） | %s |" % (
                 "`%s`（由 `dist/%s.content.sha256` 旁证给出，同一棵树重建必同值）"
                 % (content_sha, deb.name) if content_sha else
                 "**未取得**：本包由旧版 build-deb.sh 构建（没有 content 旁证），重跑一次即可"),
             "| 包成员 | %d 个文件（%s） |" % (
                 len(members), "、".join("`%s/*` %d" % (k, len(v)) for k, v in sorted(tops.items()))),
             "| 入口 | `okb --help` 子命令 **%d** 个（由 `okb/cli.py` 的 COMMANDS 计数） |" % subcommand_count(),
             "| 安装态自检项数 | **%d** 项（由 `deploy/openkylin-verify.sh` 的 check 调用数算出） |" % verify_check_count()]
    if rec:
        r, name = rec
        lines.append("| 安装态自检结果 | %s / Python %s → **pass=%d fail=%d**（回执 `%s`） |" % (
            r["os"], r["python"], r["passed"], r["failed"], name))
        lines.append("| 自检覆盖交付卷 | %s |" % (
            "是（`delivery_*` 项存在）" if any(c["name"].startswith("delivery_") for c in r["checks"]) else "**否**"))
    lines += ["| 真机（openKylin）验证 | **未取得**：本表全部数值来自 Debian 系代理环境（WSL2），"
              "不能替代真机 `dpkg -i` / `lintian` / 真 Agent 跑批 |",
              END, ""]
    text = "\n".join(lines)
    if out:
        pathlib.Path(out).write_text(text, encoding="utf-8", newline="\n")
        print("已写出生块：", out)
        return 0
    doc = TARGET.read_text(encoding="utf-8")
    if BEGIN not in doc or END not in doc:
        raise SystemExit("appendix 里找不到 DEB-FACTS 标记：先插入标记再运行")
    head = doc.split(BEGIN)[0]
    tail = doc.split(END, 1)[1]
    TARGET.write_text(head + text + tail, encoding="utf-8", newline="\n")
    print("已重写", TARGET, "的成员/大小/sha/自检事实")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
