"""评审文档不得与产物漂移：直接跑 tools/audit_review_claims.py，要求退出码 0。
这条测试的意义是把我犯过的那类错误变成机器守住的规则：文档里的每个计数、每张表、
每个被引用的指纹，都必须能从矩阵/计划/试卷/交付包重算出来。文档写错一个数字，
套件就红，而不是等评委发现。
"""
from pathlib import Path
import pathlib
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(ROOT / 'tools' / 'tests')]

ROOT = pathlib.Path(__file__).resolve().parents[2]

# 这两组用例审计的是旧仓（okb-int）冻结链产物：cards-r3 → freeze_candidates →
# notes/paper-r1-coverage-matrix.json。okb-v2 走自有生成器链（v2/02-generators），
# 冻结产物不在本工作区；缺它时如实跳过而不是假红（同 test_judge_drift 的守卫模式）。
FREEZE_ARTEFACTS_PRESENT = (ROOT / 'notes/paper-r1-coverage-matrix.json').is_file()


@unittest.skipUnless(FREEZE_ARTEFACTS_PRESENT,
                     "旧仓冻结产物（cards-r3 编译链）不在 okb-v2；v2 走自有生成器链")
class DocClaimTests(unittest.TestCase):
    def test_review_document_claims_match_artefacts(self):
        out = subprocess.run([sys.executable, str(ROOT / 'tools/audit_review_claims.py')],
                             cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', errors='replace',
                             timeout=300)
        drift = [l for l in (out.stdout or '').splitlines() if l.startswith('FAIL')]
        self.assertEqual(out.returncode, 0,
                         'review document drifted from artefacts:\n' + '\n'.join(drift[:10]))
        self.assertIn('0 项漂移', out.stdout)

    def test_auditor_itself_detects_a_wrong_number(self):
        """自检器必须能抓错：临时把文档里的一个计数改坏，它要红。"""
        doc = ROOT / 'docs/review-design-v1.md'
        original = doc.read_text(encoding='utf-8')
        self.assertIn('wrongly_reused 20', original, '被检字符串不在文档里，测试就失去意义')
        try:
            doc.write_text(original.replace('wrongly_reused 20', 'wrongly_reused 77', 1),
                           encoding='utf-8', newline='\n')
            out = subprocess.run([sys.executable, str(ROOT / 'tools/audit_review_claims.py')],
                                 cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', errors='replace',
                             timeout=300)
            self.assertEqual(out.returncode, 2, '自检器没抓到被改坏的文档数字')
            self.assertIn('wrongly_reused', out.stdout)
        finally:
            doc.write_text(original, encoding='utf-8', newline='\n')
            self.assertEqual(doc.read_text(encoding='utf-8'), original, '恢复失败会污染仓库')

class PendingEvidencePredicateTests(unittest.TestCase):
    """「未取得」清单的谓词必须可判定、可失败、且默认保守（拿不到数据=未完成）。"""

    def fake_root(self, kind):
        import json
        import tempfile
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(str(root), ignore_errors=True))
        (root / "notes").mkdir()
        if kind == "license":
            (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
        if kind == "real-run":
            (root / "notes" / "openkylin-real-run-receipt.json").write_text("{}", encoding="utf-8")
        if kind == "labels":
            d = root / "handoff" / "split" / "rater-a"
            d.mkdir(parents=True)
            (d / "answers.jsonl").write_text(json.dumps({"state": "closed"}) + "\n", encoding="utf-8")
        if kind == "600":
            m = root / "runs" / "r1"
            m.mkdir(parents=True)
            (m / "manifest.json").write_text(json.dumps(dict(
                agents=[dict(name="openclaw", agent_kind="real"), dict(name="hermes", agent_kind="real")],
                tasks=[dict(task_id="t%d" % k) for k in range(600)])), encoding="utf-8")
        return root

    def evidence(self, root):
        from audit_review_claims import pending_items
        return {label: present for label, _phrase, present, _why in pending_items(root, "", "")}

    def test_empty_root_reports_nothing_done(self):
        got = self.evidence(self.fake_root("empty"))
        self.assertEqual(sorted(got), ["LICENSE / 权利人", "dynamic_update 三相语义",
                                       "openKylin 真机验证",
                                       "真人标注（判官 vs 人 κ）", "真实两 Agent 600 任务跑批"])
        self.assertTrue(all(v is False for v in got.values()), got)

    def test_each_evidence_signal_is_detected(self):
        cases = {"license": "LICENSE / 权利人", "real-run": "openKylin 真机验证",
                 "labels": "真人标注（判官 vs 人 κ）", "600": "真实两 Agent 600 任务跑批"}
        for kind, label in cases.items():
            with self.subTest(kind=kind):
                got = self.evidence(self.fake_root(kind))
                self.assertTrue(got[label], "%s 的证据没被识别" % label)
                self.assertEqual(sum(1 for v in got.values() if v), 1,
                                 "识别越界：其它条目不应同时变真")

    def test_real_repo_still_has_no_completion_evidence(self):
        """当前事实核对：任何一条谓词在真实仓库里变真，就说明"未取得"清单需要同步更新。"""
        got = self.evidence(Path(__file__).resolve().parents[2])
        self.assertTrue(all(v is False for v in got.values()),
                        "有未取得项已取得证据，文档需同步：%s" % [k for k, v in got.items() if v])


@unittest.skipUnless(FREEZE_ARTEFACTS_PRESENT,
                     "旧仓冻结产物（cards-r3 编译链）不在 okb-v2；v2 走自有生成器链")
class FingerprintKnownSetTests(unittest.TestCase):
    """文档引用的包指纹在 dist/ 缺席时也必须可核对——否则结论按运行顺序变。

    现场复现过的缺陷（VM 等价树：无 build/ dist/ runs/）：
      FAIL 文档引用的每个指纹都对应现存产物
           dead=[('b05c73869deb', ['docs/HANDOVER_B_2026-10-08.md']), ('b66f235171ad', [...])]
    两个原因叠在一起：
      ① 这条核对自带 `build_absent` 豁免，而 run_suite 里别的套件会在审计之前把 build/ 造出来
         ⇒ 同一棵树、同一份文档，先跑谁决定红绿（交接文档 §2.2 修过两处同类，这是第三处）；
      ② .deb 的**容器 sha 绑定构建实例**（mtime/gzip 头），跨机必然不同值 —— 把它当"必须在本机
         磁盘上找得到"的引用值，在别人机器上必然假红（§2.2 自己写过这句话）。
    修法与既有 r2 凭据同款：已入库、且被 test_deb_facts_generator 盯着的 DEB-FACTS 表就是权威来源。
    """

    def deb_fact_tokens(self):
        """现场读 DEB-FACTS 表里的 64 位指纹，取文档引用用的 12 位前缀。

        不手抄：第一版这里写死了上一轮包的两个前缀，重建 .deb 之后守卫自己变成了过期期望值
        （红得莫名其妙，还把人引向"审计器坏了"）。期望值一律现场读盘。
        """
        import re
        appx = (ROOT / "deploy/RUN_ON_OPENKYLIN-appendix.md").read_text(encoding="utf-8")
        b = appx.find("<!-- DEB-FACTS:BEGIN")
        e = appx.find("<!-- DEB-FACTS:END -->")
        self.assertTrue(0 <= b < e, "附录里没有 DEB-FACTS 标记区")
        toks = sorted({m[:12] for m in re.findall(r"\b[0-9a-f]{64}\b", appx[b:e])})
        self.assertGreaterEqual(len(toks), 2, "表里应至少有容器 sha 与内容指纹两个值：%s" % toks)
        return toks


    CITED_DOC = "README.md"          # 在审计器的扫描清单里

    def _tree(self, mangle_facts: bool):
        import shutil
        import testtmp
        dst = pathlib.Path(testtmp.mkdtemp(prefix="okb-audit-fp-"))
        self.addCleanup(lambda: shutil.rmtree(str(dst), ignore_errors=True))
        # dist/ 与 runs/ 不在 VM 同步范围内；build/ 留着（模拟"别的套件先跑过"的顺序）
        ign = shutil.ignore_patterns(".git", "dist", "runs", "__pycache__", "*.pyc")
        for item in ROOT.iterdir():
            if item.name in ("dist", "runs", ".git"):
                continue
            if item.is_dir():
                shutil.copytree(item, dst / item.name, ignore=ign, symlinks=True)
            else:
                shutil.copy2(item, dst / item.name)
        self.assertFalse((dst / "dist").exists(), "复制没排掉 dist/，本用例就测不到东西")
        self.assertTrue((dst / "build").is_dir(), "缺 build/ 会走豁免分支，测不到真缺陷")
        # 自带引用样本：把 DEB-FACTS 表里的一个包指纹按文档写法（12 位 + 省略号）引一次。
        # 不依赖真实文档当前恰好抄了它——我曾把包指纹从散文里撤掉，反向用例当场失去触发条件、
        # 变成"永远绿"。守卫必须自己造被检对象。
        cited = dst / self.CITED_DOC
        text = cited.read_text(encoding="utf-8")
        tok = self.deb_fact_tokens()[0]
        cited.write_text(text + "\n\n<!-- 守卫样本：交付包内容指纹 `%s\u2026` -->\n" % tok,
                         encoding="utf-8", newline="\n")
        self.cited_token = tok
        if mangle_facts:
            appx = dst / "deploy/RUN_ON_OPENKYLIN-appendix.md"
            atext = appx.read_text(encoding="utf-8")
            self.assertIn("<!-- DEB-FACTS:BEGIN", atext)
            appx.write_text(atext.replace("<!-- DEB-FACTS:BEGIN", "<!-- DEB-FACTS-OFF:BEGIN", 1)
                                 .replace("<!-- DEB-FACTS:END -->", "<!-- DEB-FACTS-OFF:END -->", 1),
                            encoding="utf-8", newline="\n")
        return dst

    def _fingerprint_line(self, root):
        # 必须跑**复制树里的那份审计器**：它用 `Path(__file__).parents[1]` 定 ROOT，
        # 只改 cwd 的话它审计的仍是真仓库，正反两个用例会得到同一个结果（第一版就是这么假绿的）。
        script = root / "tools/audit_review_claims.py"
        self.assertTrue(script.is_file(), "复制树里没有审计器")
        out = subprocess.run([sys.executable, str(script)],
                             cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=600)
        blob = (out.stdout or "") + (out.stderr or "")
        # 审计器里有两条名字相近的核对（§5.x 的"…现存可核对产物"针对评审主文档、
        # §5.9 的"…现存产物"针对全部文档的指纹守卫）。必须按**全名**取，否则一条断言盯两条输出。
        hits = [l for l in blob.splitlines() if "文档引用的每个指纹都对应现存产物" in l]
        self.assertEqual(len(hits), 1, "没找到 §5.9 那条核对的输出：%s" % hits)
        return hits[0], blob

    def test_cited_package_fingerprint_resolves_without_dist(self):
        """正向：dist/ 缺席、只有 DEB-FACTS 表时，文档引用的包指纹仍必须可核对。"""
        line, _ = self._fingerprint_line(self._tree(mangle_facts=False))
        self.assertTrue(line.startswith("PASS"), line)

    def test_mutation_removing_the_credential_table_is_detected(self):
        """反向变异：把 DEB-FACTS 凭据表拿掉 ⇒ 必须红，并点名那条死引用。"""
        root = self._tree(mangle_facts=True)
        line, _ = self._fingerprint_line(root)
        self.assertTrue(line.startswith("FAIL"),
                        "拿掉凭据表后仍判通过 ⇒ 这条守卫其实没在读表，是永远绿的假自检：" + line)
        self.assertIn(self.cited_token, line,
                      "死引用没被点名：%s（输出：%s）" % (self.cited_token, line))
if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
