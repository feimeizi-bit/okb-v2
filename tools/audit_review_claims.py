#!/usr/bin/env python3
"""评审文档一致性自检：docs/review-design-v1.md 里的数字必须由产物重算，而不是我记得。

存在的理由（都是真实踩过的坑）：
- 文档曾引用一个 39 题时代构建的 .deb，声称"安装态内嵌 100 题卷"——拿错期望值比对导致假通过；
- 六维表把"边界识别·卡片自带 15"写成 5；卡闸算术写成 98（实际 83 过闸 / 75 选中）；
- .deb 容器 sha 每次构建都不同（gzip 头时间戳），早期文档暗示包级可复现，过头了。

因此这里只做机器可判定的核对：文档字符串 vs 矩阵/计划/试卷/包的实测指纹。
退出码 0 = 全部一致；2 = 有任何一条漂移（并列出期望与实际）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/review-design-v1.md"
MATRIX = ROOT / "notes/paper-r1-coverage-matrix.json"
PLAN = ROOT / "samples/four-layer-r1.json"
PAPER_YAML = ROOT / "samples/paper-r1.yaml"
PAPER_JSONL = ROOT / "build/paper-r1.jsonl"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pending_items(root: pathlib.Path, doc: str, readme: str) -> list:
    """「未取得」清单 <-> 现实证据。返回 (标签, 文档短语, 证据是否已出现, 证据说明)。

    可判定就常驻：真机回执/已交卷的标注文件/≥2 真实适配器的 600 任务跑批/动态更新行数/
    录屏文件/LICENSE。证据出现而文档仍列为未取得 → 调用方报漂移；证据没有而文档删了这条
    → 同样报漂移。谓词一律**保守**：拿不到数据返回 False/0，绝不猜已完成。
    """
    import glob as _glob

    plan = root / "samples" / "four-layer-r1.json"

    def _closed_answers():
        n = 0
        for f in _glob.glob(str(root / "handoff" / "**" / "answers*.jsonl"), recursive=True) + \
                 _glob.glob(str(root / "handoff" / "**" / "labels*.jsonl"), recursive=True):
            try:
                head = pathlib.Path(f).read_text(encoding="utf-8").splitlines()[0]
            except Exception:
                continue
            if '"closed"' in head or '"signed"' in head:
                n += 1
        return n

    def _real_multi_agent_run():
        for m in _glob.glob(str(root / "runs" / "*" / "manifest.json")):
            try:
                data = json.loads(pathlib.Path(m).read_text(encoding="utf-8"))
            except Exception:
                continue
            # 数的是**不同 agent 名**，不是 kind 集合：两个真 Agent 的 kind 都叫 real，
            # 用 kind 去重会永远得到 1（谓词自身的那个 bug 是被这条测试抓出来的）
            real = {a.get("name") for a in data.get("agents", [])
                    if a.get("agent_kind") in ("real", "cli-template")}
            if len(real) >= 2 and len(data.get("tasks", [])) >= 600:
                return m
        return None

    def _dynamic_update_rows():
        try:
            pl = json.loads(plan.read_text(encoding="utf-8"))
        except Exception:
            return -1
        return sum(1 for s in pl.get("samples", []) if s.get("dimension") == "dynamic_update")

    closed = _closed_answers()
    dyn = _dynamic_update_rows()
    return [
        ("openKylin 真机验证", "openKylin 真机",
         (root / "notes" / "openkylin-real-run-receipt.json").is_file(),
         "notes/openkylin-real-run-receipt.json 出现就说明真机已验证"),
        ("真人标注（判官 vs 人 κ）", "真人标注", closed > 0,
         "handoff 下存在已 closed 的答案文件 %d 份" % closed),
        ("真实两 Agent 600 任务跑批", "600 任务", _real_multi_agent_run() is not None,
         "runs/*/manifest.json 中存在 ≥2 真实适配器且 ≥600 任务"),
        ("dynamic_update 三相语义", "dynamic_update", dyn > 5,
         "计划里 dynamic_update 行数 = %s（>5 说明已扩枚举）" % dyn),
        ("LICENSE / 权利人", "LICENSE", (root / "LICENSE").is_file(), "仓库根出现 LICENSE"),
    ]


def main(argv=None) -> int:
    if not DOC.is_file():
        print("missing document: " + str(DOC), file=sys.stderr)
        return 2
    doc = DOC.read_text(encoding="utf-8")
    if not (MATRIX.is_file() and PLAN.is_file() and PAPER_YAML.is_file()):
        print("artefacts not generated yet; run tools/freeze_candidates.py first", file=sys.stderr)
        return 2
    # 全新 clone 时 build/ 被 .gitignore 排除：试卷由仓库内的题面重建（确定性、离线），
    # 否则"计划是否绑定当前试卷"这条在干净环境下会假红。
    if not PAPER_JSONL.is_file():
        subprocess.run([sys.executable, str(ROOT / "tools/build_paper.py"), str(PAPER_YAML),
                        "-o", str(PAPER_JSONL)], cwd=str(ROOT), capture_output=True, check=False)
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    results = []

    def check(name, ok, expected='', actual='', skip=False):
        # skip=True：这台机器上没有该输入（build/ dist/ 不在同步范围），既不算通过也不算漂移。
        # 单独计数打印，是为了不让人把"80 项 0 漂移"读成"全都核对过了"。
        results.append((ok, name, expected, actual, bool(skip)))

    # 1) 总量：文档必须引用矩阵实测值
    t = matrix['totals']
    for field, label in (('paper_samples', '题数'), ('plan_rows', '计划行'),
                         ('plan_checks', '强制检查'), ('paper_anchors', '锚点'),
                         ('paper_fixture_files', '初始工件'), ('compiled_samples', '编译卡')):
        check('文档含 %s=%d' % (label, t[field]), str(t[field]) in doc, t[field], 'in doc?')
    check('卡闸算术自洽 (compiled+uncompilable+cap == 100)',
          t['compiled_samples'] + t['uncompilable_cards'] + t['cap_rejected_cards'] == 100,
          100, t['compiled_samples'] + t['uncompilable_cards'] + t['cap_rejected_cards'])
    # 必须按"数字边界"找：裸子串会被 439,980 这种字节数误报（实测踩过，检查器自身有假阳性）
    stray98 = re.findall(r'(?<![\d,])98(?!\d)', doc)
    check('文档不出现已废弃的卡闸数字 98（独立成数）', not stray98, "absent", "%d 处" % len(stray98))

    # 2) 六维表逐格核对
    zh = {'长期保持': 'long_term_retention', '记忆调用': 'memory_recall', '动态更新': 'dynamic_update',
          '相近区分': 'similar_discrimination', '边界识别': 'boundary_recognition',
          '任务复用': 'task_reuse'}
    rows = {c[0]: (int(c[1]), int(c[2])) for c in re.findall(
        r'\| (长期保持|记忆调用|动态更新|相近区分|边界识别|任务复用) \| (\d+) \| \*{0,2}(\d+)', doc)}
    check('六维表六行齐全', len(rows) == 6, 6, len(rows))
    for cn, en in zh.items():
        if cn not in rows:
            continue
        doc_rows, doc_cards = rows[cn]
        real = matrix['dimensions'][en]
        check('%s plan 行一致' % cn, doc_rows == real['plan_rows'], real['plan_rows'], doc_rows)
        check('%s 卡片自带行一致' % cn, doc_cards == real['plan_rows_from_cards_only'],
              real['plan_rows_from_cards_only'], doc_cards)

    # 3) 四类证据 / 五类结果
    ev = {k: v['total'] for k, v in matrix['evidence_sources']['semantic_scopes'].items()}
    for k, v in ev.items():
        check('证据计数 %s=%d 出现在文档' % (k, v), ('%s %d' % (k, v)) in doc, v, 'doc string')
    of = {k: v['total'] for k, v in matrix['error_results']['plan_on_failure_by_check'].items()}
    for k, v in of.items():
        check('失败类计数 %s=%d 出现在文档' % (k, v), ('%s %d' % (k, v)) in doc, v, 'doc string')

    # 4) 指纹：文档引用的 sha 前缀必须能在产物上找到（防"引用旧构建产物"）
    real_prefixes = {sha(PAPER_YAML)[:16]: 'samples/paper-r1.yaml',
                     sha(PLAN)[:16]: 'samples/four-layer-r1.json'}
    if PAPER_JSONL.is_file():
        real_prefixes[sha(PAPER_JSONL)[:16]] = 'build/paper-r1.jsonl'
    # 交付包（仓库外 handoff 目录）若存在也纳入可比对集合，并核对文档引用的字节数
    deb = pathlib.Path(r'D:/a_shanghai/handoff/okb_0.1.0_all.deb')
    dist_deb = ROOT / "dist/okb_0.1.0_all.deb"
    if deb.is_file():
        real_prefixes[sha(deb)[:16]] = str(deb)
        size = deb.stat().st_size
        # 事实可以写在任一篇文档里（附录的生成表也算）：要求"两处都抄一遍"只会制造下一次过期
        texts = doc
        for extra in ("deploy/RUN_ON_OPENKYLIN-appendix.md", "deploy/RUN_ON_OPENKYLIN.md",
                      "README.md"):
            q = ROOT / extra
            if q.is_file():
                texts += "\n" + q.read_text(encoding="utf-8")
        if dist_deb.is_file():
            check('交接位与 dist/ 是同一份包（不是旧构建残留）', sha(deb) == sha(dist_deb),
                  sha(dist_deb)[:12], sha(deb)[:12])
    cited = set(re.findall(r'`([0-9a-f]{8,16})\u2026', doc)) | set(re.findall(r'\b([0-9a-f]{16})\b', doc))
    unknown = [c for c in cited
               if not any(p.startswith(c) or c.startswith(p) for p in real_prefixes)]
    # 文档可以引用 build/ 下的产物（如 r2 的 paper-r2.jsonl），而 build/ 既不进 .deb、
    # 也不在 deploy/sync-to-vm.ps1 的同步清单里：这种引用在目标机上**无法核对**。
    # 报漂移会把环境缺料说成缺陷，报通过是撒谎 —— 用 SKIP 单列。
    unverifiable = [c for c in unknown if not (ROOT / "build").is_dir()]
    hard_unknown = [c for c in unknown if (ROOT / "build").is_dir()]
    if unverifiable:
        check('文档引用的每个指纹都对应现存可核对产物', False, 'none',
              "本机无 build/，以下引用无法核对：%s" % sorted(set(unverifiable))[:4], skip=True)
    check('文档引用的每个指纹都对应现存可核对产物', not hard_unknown, 'none', hard_unknown[:4])

    # 5) 计划自证：plan 的行数/检查数与矩阵一致（矩阵不是手填的）
    check('plan 行数等于矩阵 plan_rows', len(plan['samples']) == t['plan_rows'],
          t['plan_rows'], len(plan['samples']))
    checks = sum(len(s['checks']) for s in plan['samples'])
    check('plan 检查数等于矩阵 plan_checks', checks == t['plan_checks'], t['plan_checks'], checks)
    check('plan 绑定当前试卷指纹',
          PAPER_JSONL.is_file() and plan['paper_sha256'] == sha(PAPER_JSONL))

    # 6) 凭据卫生：跟踪文件里不得出现 .env 中的真实凭据
    env = {}
    root_env = ROOT / '.env'
    if root_env.is_file():
        for line in root_env.read_text(encoding='utf-8', errors='replace').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    secrets = [v for k, v in env.items()
               if any(s in k.upper() for s in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and len(v) >= 12]
    tracked = subprocess.run(['git', 'ls-files'], capture_output=True, text=True,
                             cwd=str(ROOT)).stdout.split()
    leaked = []
    for f in tracked:
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 4_000_000:
            continue
        data = p.read_bytes()
        if any(s.encode() in data for s in secrets):
            leaked.append(f)
    check('跟踪文件零凭据（扫描 %d 个秘密值 / %d 个文件）' % (len(secrets), len(tracked)), not leaked, [], leaked[:3])

    # ── 5.5) 交付包：把"可跨机核对的内容指纹"和"只绑定本机构建实例的容器 sha"分开判。
    # 容器 sha256 覆盖 mtime/gzip 头，换台机器同树重建也对不上——把它当作文档引用值去核，
    # 在别人机器上必然假红（A 侧 VM 就吃过一次）。内容指纹（每个成员路径+内容哈希的摘要）
    # 由 build-deb.sh 落旁证，同树必同值，才是该写进文档的量。
    alltext = doc
    for extra in ("deploy/RUN_ON_OPENKYLIN-appendix.md", "deploy/RUN_ON_OPENKYLIN.md", "README.md"):
        q = ROOT / extra
        if q.is_file():
            alltext += "\n" + q.read_text(encoding="utf-8")
    for deb in sorted((ROOT / "dist").glob("okb_*.deb")) if (ROOT / "dist").is_dir() else []:
        real = sha(deb)
        size = deb.stat().st_size
        side = deb.parent / (deb.name[:-4] + ".content.sha256")
        content = side.read_text(encoding="utf-8").strip() if side.is_file() else None
        if content:
            check("附录引用的 .deb 内容指纹 == 本机包旁证 (%s)" % deb.name,
                  content in alltext or content[:16] in alltext, content[:16], "未出现在任何文档")
        if real[:8] in alltext:
            check("本机 .deb 容器 sha 与文档引用一致 (%s)" % deb.name, True, real[:8], real[:8])
        else:
            check("本机 .deb 容器 sha 与文档引用一致 (%s)" % deb.name, False, real[:8],
                  "本机包是另一次构建实例（容器 sha 随构建变化）；%s"
                  % ("内容指纹一致，视为同一内容" if content else "无内容旁证，无法跨机判定"),
                  skip=True)
        if str(size) in alltext or f"{size:,}" in alltext:
            check("本机 .deb 字节数与文档引用一致 (%s)" % f"{size:,}", True, size, size)
        else:
            check("本机 .deb 字节数与文档引用一致 (%s)" % f"{size:,}", False, size,
                  "同上：容器级事实只在本机构建机上可比", skip=True)
        break

    # ── 5.6) 「未取得」清单必须与现实同步：证据出现而文档还说没做 → 红；
    #          证据确实没有而文档把它删了 → 也红。此前这类过期只能靠人记得去查。
    # rd/readme 必须在函数内首次使用前定义（Python 的函数级作用域，不看执行顺序）
    rd = doc
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").is_file() else ""
    # ── 5.6) 「未取得」清单必须与现实同步：证据出现而文档还说没做 → 红；
    for label, phrase, evidence_present, why in pending_items(ROOT, rd, readme):
        listed = phrase in rd or phrase in readme
        if evidence_present:
            check("未取得清单仍列 %s（但证据已出现：%s）" % (label, why), not listed,
                  "文档应改为已完成并给出复算方式", "仍列为未取得")
        else:
            check("未取得清单含 %s" % label, listed, phrase, "文档缺失该条")

    # ── 5.7) r2 候选卷：凭据必须能对上实际文件；没生成就不许在文档里声称它有
    CRED = ROOT / "notes/paper-r2-candidate.json"
    R2_PLAN = ROOT / "samples/four-layer-r2.json"
    R2_JSONL = ROOT / "build/paper-r2.jsonl"
    if CRED.is_file() and not (R2_PLAN.is_file() and R2_JSONL.is_file()):
        # 凭据在、产物不在（VM/scp 树不含 build/）：只能声明"这台机器无法核对"，
        # 不能崩（rc=1 会让上层把它当产品缺陷），也不能因此就说 r2 已验证。
        check("r2 候选卷在本机可核对", False, "计划与 jsonl 都在",
              "缺 %s（build/ 不在同步范围，属维护者产物）"
              % "、".join(str(x.relative_to(ROOT)) for x in (R2_PLAN, R2_JSONL) if not x.is_file()),
              skip=True)
    if CRED.is_file() and R2_PLAN.is_file() and R2_JSONL.is_file():
        cred = json.loads(CRED.read_text(encoding="utf-8"))
        plan2 = json.loads(R2_PLAN.read_text(encoding="utf-8"))
        rows2 = [json.loads(l) for l in R2_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
        scope2 = collections2 = None
        import collections as _c
        scope2 = _c.Counter(c.get("scope") for smp in plan2["samples"] for c in smp["checks"])
        check("r2 凭据的题数与文件一致", cred["samples"] == len(rows2) == 104,
              104, (cred["samples"], len(rows2)))
        check("r2 凭据的 paper_sha256 与磁盘一致", cred["paper_sha256"] == sha(R2_JSONL),
              sha(R2_JSONL)[:12], cred["paper_sha256"][:12])
        check("r2 凭据的 plan_sha256 与磁盘一致", cred["plan_sha256"] == sha(R2_PLAN),
              sha(R2_PLAN)[:12], cred["plan_sha256"][:12])
        check("r2 真的补上了任务前基线（memory_before > 0）", scope2["memory_before"] >= 4,
              ">=4", scope2["memory_before"])
        check("r2 不改动 r1（凭据声明且指纹实测不变）",
              cred["r1_untouched"] is True
              and cred["r1_paper_sha"] == sha(ROOT / "samples/paper-r1.yaml")
              and cred["r1_plan_sha"] == sha(ROOT / "samples/four-layer-r1.json"),
              "r1 三份指纹不变", "drift")
        check("文档把 r2 标为候选卷而非交付卷",
              "候选" in rd and "paper-r2" in rd, "r2 标注为候选", "missing")
    elif CRED.is_file():
        # build/ 不进 .deb、也不在 deploy/sync-to-vm.ps1 清单里：这台机器上"r2 到底生成没有"
        # 根本判不了。判成漂移会把环境缺料说成缺陷；判成通过是撒谎。
        check("文档把 r2 标为候选卷而非交付卷（本机缺 build 产物，按已入库凭据判）",
              "候选" in rd and "paper-r2" in rd, "r2 标注为候选", "文档没有把 r2 标成候选卷")
    else:
        check("未生成 r2 时文档不得声称其存在", "paper-r2" not in rd, "absent", "文档已引用 r2")

    # ── 5.75) 冻结就绪度：凭据必须等于重算，且回执必须引用它（防"回执里的数"与"机器量的数"分家）
    FS = ROOT / "notes/freeze-status-v1.0.json"
    REPLY = ROOT / "docs/B-reply-A-freeze-F1-F7-2026-10-08.md"
    if FS.is_file() and REPLY.is_file():
        import subprocess as _sp
        chk = _sp.run([sys.executable, str(ROOT / "tools/freeze_status.py"), "--check"],
                      cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        check("冻结就绪度凭据 == 现场重算（freeze_status --check）", chk.returncode == 0,
              "rc=0", "rc=%s %s" % (chk.returncode, (chk.stderr or chk.stdout or "")[-160:]))
        fdoc = json.loads(FS.read_text(encoding="utf-8"))
        # 变量名教训：上一版这里复用 rd，把 §5.9 的 rd（评审主文档）覆盖成了回执文本，
        # 于是"文档不得把归因说成第七维能力分"开始对着错误的文件报警。局部变量必须带来源。
        reply_text = REPLY.read_text(encoding="utf-8")
        for _n, _lbl in ((fdoc["red_line_1_confusion"]["dims_ok"], "红线1 达标维数"),
                         (fdoc["red_line_2_symmetry"]["gap_literal"], "红线2 字面正反差"),
                         (fdoc["collection_ceiling"]["items_without_fixture"], "无 fixture 题数"),
                         (fdoc["human_review"]["items_self_declared_not_human_reviewed"], "自述未人审题数")):
            check("回执引用了 %s=%d" % (_lbl, _n), str(_n) in reply_text, _n, "回执文本里找不到")
        # "本文不宣布 v1.0 正式卷冻结生效"是**否认句**，裸子串会把它当成违规声明（上一版就误报过）。
        # 按句判定：出现"冻结…生效"的句子必须自带否定词，否则才算无条件声明。
        NEG = ("不", "未", "否", "严禁", "不得", "没有")
        claims = [seg for seg in re.split(r"[。\n]", reply_text)
                  if re.search(r"冻结.{0,6}生效", seg) and not any(n in seg for n in NEG)]
        check("红线未清零时回执不得无条件声称冻结生效",
              bool(not fdoc["red_lines_failed"]) or not claims,
              "只能说提案", claims[:1])
    else:
        check("冻结就绪度凭据与 F1-F7 回执都在仓内", False, "两份都要有",
              [str(p.relative_to(ROOT)) for p in (FS, REPLY) if not p.is_file()])

    # ── 5.755) 红线1 三读法：验收读法达标不许让另外两个读法从凭据里消失
    # 起因：三读法并报是 §8 R-2 登记的处置，最容易在"下次整理凭据"时被简化成一个好看的数。
    # 这条核对盯的是**结构**（三个读法都在、验收读法与红线1 的过/未过一致），不盯具体数值。
    for fsc in sorted((ROOT / "notes").glob("freeze-status-*.json")):
        fdoc2 = json.loads(fsc.read_text(encoding="utf-8"))
        rl1 = (fdoc2.get("red_line_1_confusion") or {})
        readings = rl1.get("readings") or {}
        acc = rl1.get("acceptance_reading")
        ok_struct = (set(readings) == {"A_anchors_literal", "B_plan_confused", "C_plan_content_error"}
                     and acc in readings
                     and all(isinstance(r.get("dims_ok"), int) for r in readings.values())
                     and all("per_dimension" in r for r in readings.values()))
        ok_consistent = bool(acc) and (
            ("红线1 每维混淆判据>=8" in (fdoc2.get("red_lines_passed") or []))
            == (readings.get(acc, {}).get("dims_ok") == rl1.get("dims_total")))
        check("红线1 三读法都在凭据里且与过/未过一致（%s）" % fsc.name,
              ok_struct and ok_consistent, "三读法齐 + 验收读法结论一致",
              dict(struct=ok_struct, consistent=ok_consistent, acceptance=acc,
                   readings=sorted(readings)))

    # ── 5.76) 冻结广播件：sha 必须等于磁盘现值，数据卡必须存在（A 拿它开跑，不能是旧件）
    for bc in sorted((ROOT / "notes").glob("freeze-broadcast-*.json")):
        b = json.loads(bc.read_text(encoding="utf-8"))
        live = {rel: sha(ROOT / rel) for rel in (b["paper"]["yaml"], b["paper"]["jsonl"], b["plan"]["path"])
                if (ROOT / rel).is_file()}
        ok_yaml = live.get(b["paper"]["yaml"]) == b["paper"]["yaml_sha256"]
        ok_jsonl = live.get(b["paper"]["jsonl"]) == b["paper"]["jsonl_sha256"]
        ok_plan = live.get(b["plan"]["path"]) == b["plan"]["sha256"]
        ok_bind = b["plan"]["bound_paper_sha256"] == b["paper"]["jsonl_sha256"]
        card = ROOT / ("docs/data-card-%s.md" % b["version"])
        check("冻结广播件 %s 的三个 sha 仍等于磁盘现值" % bc.name,
              ok_yaml and ok_jsonl and ok_plan and ok_bind,
              "卷/计划/绑定同源一致",
              dict(yaml=ok_yaml, jsonl=ok_jsonl, plan=ok_plan, bind=ok_bind))
        check("广播件的数据卡存在且由命令生成", card.is_file(), "docs/data-card-%s.md" % b["version"],
              "missing" if not card.is_file() else "ok")
        if card.is_file():
            ct = card.read_text(encoding="utf-8")
            check("数据卡里的卷 sha 与广播件一致", b["paper"]["jsonl_sha256"] in ct,
                  b["paper"]["jsonl_sha256"][:16], "卡里找不到该 sha")

    # ── 5.8) 归因读数：文档引用的数字必须等于凭据里重算出来的值
    ATTR = ROOT / "notes/baseline-attribution-mock-r2.json"
    if ATTR.is_file():
        att = json.loads(ATTR.read_text(encoding="utf-8"))
        check("归因凭据的 never_written_share 与文档引用一致",
              ("never_written_share = %s" % att["never_written_share"]) in rd
              or ("%s" % att["never_written_share"]) in rd,
              att["never_written_share"], "in doc?")
        check("归因凭据与四象限计数自洽（share 由 counts 重算）",
              abs(att["never_written_share"] -
                  (att["attribution_counts"].get("never_written", 0) / att["tasks_with_both_snapshots"]))
              < 1e-9 if att["tasks_with_both_snapshots"] else True,
              "share == counts/n", att["never_written_share"])
        joined = " ".join(att["limits"])
        # 实质判定：必须明确"不参与评分/不排名"，且队列字段自带 counts_toward_score=false 语义。
        # 早期写法要求"每条 limit 都含否定词"，被一条"恒为 false"的表述判红过——那是核对脆弱，不是漂移。
        check("归因工具声明它是诊断量而非评分",
              len(att["limits"]) >= 3 and "不参与评分" in joined and "不排名" in joined
              and "counts_toward_score" in joined,
              "limits 明确不作评分/不排名", att["limits"][:1])

    # ── 5.9) 通用守卫：文档里出现的任何 12 位 sha 前缀，都必须对得上某个现存产物。
    # 起因：我有一次核对"重生成是否逐字节一致"时读的是刚被覆写的凭据（循环验证，无效）。
    # 循环验证没法禁止，但"引用一个已经不存在的指纹"必须当场变红。
    known = {}                                     # 完整 sha -> 产物名（前缀匹配必须拿完整值比）
    for label, rel in (("r1 题卷", "samples/paper-r1.yaml"), ("r1 考卷", "build/paper-r1.jsonl"),
                       ("r1 计划", "samples/four-layer-r1.json"),
                       # dev.2 = dev.1 的卷（逐字节不变）+ 新计划；文档会引用它的指纹
                       ("r1 计划 dev.2", "samples/four-layer-r1-dev2.json"),
                       ("pilot 考卷", "build/paper-v0.2.jsonl"),
                       ("pilot 计划", "samples/four-layer-pilot.v1.json"),
                       ("pilot 题卷", "samples/pilot-v0.2.yaml"),
                       ("r2 考卷", "build/paper-r2.jsonl"), ("r2 计划", "samples/four-layer-r2.json"),
                       ("r2 题卷", "samples/paper-r2.yaml"),
                       ("交付包", "dist/okb_0.1.0_all.deb")):
        q = ROOT / rel
        if q.is_file():
            known[sha(q)] = label
    for deb in sorted((ROOT / "dist").glob("okb_*.deb")) if (ROOT / "dist").is_dir() else []:
        known[sha(deb)] = deb.name
        side = deb.parent / (deb.name[:-4] + ".content.sha256")
        if side.is_file():      # 内容指纹才是该写进文档的量（跨机可比），所以它也得进"已知产物"集合
            known[side.read_text(encoding="utf-8").strip()] = "%s（内容指纹）" % deb.name
    for extra in (ROOT / "handoff").glob("*.deb") if (ROOT.parent / "handoff").is_dir() else []:
        pass                                       # 交接位不在仓内，不参与核对
    # 包事实表（DEB-FACTS 标记区）里的指纹也是"已知产物"：dist/ 不在同步范围内，
    # 而容器 sha 本来就跨机不可比。表由 make_deb_appendix_facts.py 生成、
    # test_deb_facts_generator 断言"表 == 重算"，所以从它取值不是循环自证。
    APPX = ROOT / "deploy/RUN_ON_OPENKYLIN-appendix.md"
    if APPX.is_file():
        atext = APPX.read_text(encoding="utf-8")
        m_begin = atext.find("<!-- DEB-FACTS:BEGIN")
        m_end = atext.find("<!-- DEB-FACTS:END -->")
        block = atext[m_begin:m_end] if 0 <= m_begin < m_end else ""
        for tok in re.findall(r"\b([0-9a-f]{64})\b", block):
            known.setdefault(tok, "交付包指纹（DEB-FACTS 表记载，dist/ 不在本机时以表为准）")
    # 已入库的 r2 凭据把"作者机上真生成并校验过的指纹"记录在案：目标机没带 build 产物时，
    # 凭据就是权威来源。上一版这里按"本机 build/ 是否存在"判定，结果同一棵树上跑完套件
    # （套件会自行重建 paper-v0.2.jsonl）之后结论会变——**依赖运行顺序的核对不是核对**。
    cred_f = ROOT / "notes/paper-r2-candidate.json"
    if cred_f.is_file():
        _c = json.loads(cred_f.read_text(encoding="utf-8"))
        for _k, _lbl in (("paper_sha256", "r2 考卷（凭据记载）"), ("plan_sha256", "r2 计划（凭据记载）"),
                         ("r1_jsonl_sha", "r1 jsonl（凭据记载）"), ("r1_paper_sha", "r1 yaml（凭据记载）"),
                         ("r1_plan_sha", "r1 plan（凭据记载）")):
            _v = _c.get(_k)
            if isinstance(_v, str) and len(_v) == 64:
                known.setdefault(_v, _lbl)
    tokens, dead = {}, {}                          # tokens=全部引用；dead=对不上现存产物的
    full_shas = sorted(known)
    for rel in ("docs/review-design-v1.md", "docs/paper-r2-cards.md", "README.md",
                "deploy/RUN_ON_OPENKYLIN.md", "deploy/RUN_ON_OPENKYLIN-appendix.md",
                "docs/HANDOVER_B_2026-10-08.md",        # 交接文档同样必须可核对：它是最会被照抄的一份
                "docs/B-reply-A-freeze-F1-F7-2026-10-08.md",
                "docs/TO_A-2026-10-08-hold-600.md",      # 600 暂缓信：引用卷指纹
                "docs/B-status-2026-10-08-dev2.md",       # dev.2 出货状态：引用卷/计划/包指纹
                "docs/TO_A-2026-10-08-dev2.md",           # 给 A 的 dev.2 出货信：四个指纹都要可核对
                "docs/data-card-v1.0-dev.1.md", "docs/data-card-v1.0-dev.2.md",
                # 交接文档是最会被照抄的一份，所以它自己也要在指纹守卫范围内（同 22930dc 的做法）
                "docs/HANDOVER_B_2026-10-08-dev2.md"):
        q = ROOT / rel
        if not q.is_file():
            continue
        # 8~16 位十六进制 + "…" 都算引用（文档既写 `0d4ffe49…` 也写 `b2ef620f61ed…`）
        for tok in re.findall(r"([0-9a-f]{8,16})…", q.read_text(encoding="utf-8")):
            tokens.setdefault(tok, set()).add(rel)
            if not any(k.startswith(tok) for k in full_shas):
                dead.setdefault(tok, set()).add(rel)
    build_absent = not (ROOT / "build").is_dir()
    check("文档引用的每个指纹都对应现存产物", (not dead) or build_absent, "全部可核对",
          ("本机无 build/，跳过判定：%s" % sorted(dead)[:3]) if build_absent
          else sorted((d, sorted(dead[d])) for d in dead)[:3],
          skip=build_absent and bool(dead))
    # 覆盖度断言用 tokens：dead 为空正是期望状态，拿它当"扫到了东西"的证据会变成永假条件
    want_known = 6 if build_absent else 8      # 无 build/ 时已知产物集合本来就只有 7 个
    check("指纹守卫确实扫到了引用（%d 处 / %d 个已知产物）" % (len(tokens), len(known)),
          len(tokens) >= 3 and len(known) >= want_known,
          ">=3 引用 & >=%d 产物" % want_known, (len(tokens), len(known)))

    # ── 5.10) 报告层归因集成：凭据自洽 + 文档不得把诊断量说成分数
    INT = ROOT / "notes/report-attribution-integration.json"
    if INT.is_file():
        itg = json.loads(INT.read_text(encoding="utf-8"))
        check("报告层归因集成的 status 为 computed", itg["attribution_status"] == "computed",
              "computed", itg["attribution_status"])
        check("集成凭据自洽：share == never_written 数 / 有成对快照任务数",
              abs(itg["never_written_share"] - itg["attribution_counts"]["never_written"]
                  / itg["tasks_with_both_snapshots"]) < 1e-9,
              1.0, itg["never_written_share"])
        check("集成凭据声明总分仍为空、未授权发布",
              itg["score_untouched"]["overall_score"] is None
              and itg["score_untouched"]["release_authorized"] is False,
              "overall=None & release=False", itg["score_untouched"])
        check("文档不得把归因说成第七维能力分",
              "第七维" not in rd and "七个维度" not in rd and "不计入六维" in rd,
              "标注为诊断量", "found")

    # ── 5.11) 反身检测：离线管线的旗标为 0 是我们对评测环境的自证；一旦非 0 必须写明
    ATTR2 = ROOT / "notes/baseline-attribution-mock-r2.json"
    if ATTR2.is_file():
        a2 = json.loads(ATTR2.read_text(encoding="utf-8"))
        fc = a2.get("flag_counts", {})
        check("离线管线自旗标记录存在（flag_counts/review_queue_size 字段）",
              "flag_counts" in a2 and "review_queue_size" in a2, "字段存在", "缺失")
        if fc:
            check("自旗标非 0 时文档必须解释（%s）" % json.dumps(fc, ensure_ascii=False),
                  "review_queue" in rd or "旗标" in rd, "文档已说明", "未说明")
        else:
            check("离线管线自旗标为 0（评测环境一致性通过）", a2.get("review_queue_size") == 0,
                  0, a2.get("review_queue_size"))

    # ── 6) 判官统计：文档里的 κ / Po / Pe / 精确下界必须由 notes 重算 ──────────
    # 缺口存在的原因：错数 0.4659（方向写反的二分产物）曾经"审计 35/35 全绿"地进了文档，
    # 因为那时审计器只核对题量/指纹，压根没看统计值。这一节就是补那道门。
    BJ = ROOT / "notes/dual-judge-batch-2026-10-04.json"
    if BJ.is_file():
        bj = json.loads(BJ.read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT / "tools"))
        import annotate_checks as AC
        n, po = bj["paired_checks"], bj["observed_agreement"]
        pe = bj.get("chance_agreement", bj.get("expected_agreement"))   # notes 里的名字是 chance_agreement
        if pe is None:
            check("notes 含机会一致率（κ 的可重算前提）", False, "chance_agreement", "missing")
            pe = po
        k = int(round(po * n))
        kappa_re = (po - pe) / (1 - pe)
        bound_re = AC.exact_lower_bound(k, n)
        check("notes κ 与 Po/Pe 自洽", abs(kappa_re - bj["cohens_kappa"]) < 5e-4,
              round(kappa_re, 4), bj["cohens_kappa"])
        check("notes 精确下界可被 annotate_checks 重算",
              abs(bound_re - bj["agreement_exact95_lower_bound"]) < 1e-3,
              round(bound_re, 4), bj["agreement_exact95_lower_bound"])
        check("文档引用的 κ 与重算值一致", ("κ=%.4f" % round(bj["cohens_kappa"], 4)) in doc,
              "κ=%.4f" % round(bj["cohens_kappa"], 4), "in doc?")
        check("文档引用的下界与重算值一致",
              ("**%.3f**" % round(bound_re, 3)) in doc or ("%.4f" % bound_re) in doc,
              "%.3f/%.4f" % (round(bound_re, 3), bound_re), "in doc?")
        check("文档不再出现那个错下界 0.466/0.4659",
              "0.466" not in doc and "0.4659" not in doc, "absent", "found!")

    # ── 7) 状态口径：README 引用的计数必须有凭据，且凭据不得比测试更旧 ─────────
    SUITE = ROOT / "notes/suite-status.json"
    check("run_suite 计数凭据存在", SUITE.is_file(), "notes/suite-status.json", "missing")
    if SUITE.is_file():
        st = json.loads(SUITE.read_text(encoding="utf-8"))
        claim = "%d 套件 / %d 用例" % (st["suites"], st["cases"])
        check("README 的套件/用例计数与凭据一致", claim in readme, claim, "in README?")
        # 故意**不**比较 failures：本审计器自己被 run_suite 调用，凭据又由那次运行写出，
        # 比较失败数会形成自指死锁（实测：绿色运行写出的凭据永远记着上一次的红）。
        # 失败数以 run_suite 当场输出/退出码为准；这里只核对"文档引用的计数有凭据、且不比测试旧"。
        newest_now = 0
        for d in ("tools/tests", "engine/tests", "tools", "engine"):
            for f in (ROOT / d).rglob("*"):
                if f.is_file() and f.suffix in (".py", ".cjs"):
                    newest_now = max(newest_now, f.stat().st_mtime)
        corpus = []
        for d in ("tools/tests", "engine/tests"):
            for f in sorted((ROOT / d).rglob("*")):
                if f.is_file() and f.suffix in (".py", ".cjs") and "__pycache__" not in f.parts:
                    corpus.append("%s:%s" % (f.relative_to(ROOT).as_posix(), sha(f)))
        want = hashlib.sha256("\n".join(corpus).encode("utf-8")).hexdigest()
        # 内容指纹，不看 mtime：换机器 / git clone / scp 之后 mtime 必然刷新，
        # A 侧 VM 上"凭据比测试旧"就是这么假红的（内容一个字节没变）。
        have = st.get("test_corpus_sha256")
        check("计数凭据对应当前测试语料（改了测试就要重跑 run_suite --json）",
              have == want if have else False, want[:12],
              (have[:12] if have else "凭据里没有 test_corpus_sha256（旧格式，重跑一次即可）"))

    # ── 8) 安装态自检：README/文档声称的检查项数必须等于脚本里的真实项数 ────────
    VSH = (ROOT / "deploy/openkylin-verify.sh").read_text(encoding="utf-8")
    static_n = len([l for l in VSH.splitlines() if l.startswith("check ")])
    loop_m = re.search(r"for t in ([a-z_0-9 ]+); do", VSH)
    real_n = static_n + (len(loop_m.group(1).split()) if loop_m else 0)
    # A 要读的文档也在范围内：本轮就抓到附录里残留"10 PASS / 0 FAIL"（旧口径）
    for rel in ("README.md", "deploy/RUN_ON_OPENKYLIN.md", "deploy/RUN_ON_OPENKYLIN-appendix.md",
                "docs/review-design-v1.md", "docs/agent-handoff-2026-10-04.md"):
        s = (ROOT / rel).read_text(encoding="utf-8") if (ROOT / rel).is_file() else ""
        if "自检" in s:
            # 收集"与自检同现"的所有计数声明（24 项 / 24 PASS / 24/24 都算），逐个必须相等；
            # 而不是要求某一种固定写法存在（附录写的是 "24 PASS / 0 FAIL"，上一版过严误判）
            claims = set()
            for line in s.splitlines():
                # 只认明确指自检项数的短语。上一版按"含自检的行 + N 项"抓，
                # 把 appendix 里"包内 deploy/* 25 项（含 … 自检脚本）"当成自检计数（假阳性）。
                claims.update(re.findall(r"(\d+)\s*项(?:离线)?自检", line))
                claims.update(re.findall(r"自检[^0-9]{0,8}(\d+)\s*项", line))
                claims.update(re.findall(r"(\d+)\s*PASS\s*/\s*\d+\s*FAIL", line))
                claims.update(re.findall(r"安装态自检\s*(\d+)/(\d+)", line)
                              and [m[0] for m in re.findall(r"安装态自检\s*(\d+)/(\d+)", line)])
            bad = sorted({c for c in claims if int(c) != real_n})
            check("%s 自检项数声明全部 == 脚本真实项数(%d)" % (rel, real_n), not bad,
                  "只允许 %d" % real_n, "发现 %s" % bad)
    check("自检脚本测的是交付卷（paper-r1），不是 pilot 卷",
          "samples/paper-r1.yaml" in VSH and "delivery_chain" in VSH, "delivery_chain", "absent")
    check("交付链任务数口径正确（100 题 × 单 mock agent × 2 轮 = 200）",
          "!= 200" in VSH and "400 任务" not in readme and "400 任务" not in VSH
          and "400 任务" not in rd, "200", "400 任务残留")
    check("自检项数从 10 项升上来的旧口径已清除",
          "10 PASS/0 FAIL" not in rd, "absent", "found 10 PASS/0 FAIL")

    # ── 9) 预登记必须是命令，不是手搓文件 ────────────────────────────────────
    cli = (ROOT / "okb/cli.py").read_text(encoding="utf-8")
    check("okb register-run 已登记进 dispatcher", '"register-run": "register_run"' in cli,
          "registered", "missing")
    op = (ROOT / "deploy/RUN_ON_OPENKYLIN.md").read_text(encoding="utf-8")
    check("A 侧操作单含预登记/跨轮汇总一节", "预登记与跨轮汇总" in op and
          "--registration-sha256" in op, "section 3.5", "missing")
    # 只抓"同一行内反斜杠后还有内容"的真 shell bug；`\` 换行后另起一行写注释是合法的
    same_line = re.findall(r"\\[^\n]*#.*", op)
    check("A 侧操作单不含续行反斜杠后接注释的坏命令", not same_line,
          "no `\\   #` on one line", same_line[:1])

    bad = [r for r in results if not r[0] and not r[4]]
    skipped = [r for r in results if r[4]]
    for ok, name, exp, act, sk in results:
        if sk:
            print('SKIP  %s   （无法核对：%s）' % (name, act))
        else:
            print('%s  %s%s' % ('PASS' if ok else 'FAIL', name,
                                '' if ok else '   期望=%r 实际=%r' % (exp, act)))
    print('\n%d 项核对，%d 项漂移，%d 项因缺输入无法核对' % (len(results), len(bad), len(skipped)))
    if skipped:
        print('  无法核对的项不是"已验证"：' + '；'.join(r[1] for r in skipped[:8]))
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
