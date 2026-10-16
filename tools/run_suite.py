#!/usr/bin/env python3
"""全量回归 runner：按**输出**判定，不看退出码。

为什么不是 `for f in test_*.py; do python "$f" || echo FAIL; done`：
仓内测试脚本按既有约定用 `unittest.main(exit=False)`，退出码恒为 0，
所以"看 rc 判绿"的循环会一路报绿 —— 这是真实踩过的坑（它让我误判过若干次"全绿"）。
本 runner 解析每个套件的 stderr/stdout：出现 FAILED / ERROR / Traceback 即判失败，
并汇总用例数与失败清单，退出码 0 仅当全部套件干净。
"""
from __future__ import annotations

import pathlib
import re
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BAD = re.compile(r'^(FAILED|ERROR:|Traceback)', re.M)

# 每套件超时按"本机实测 × 余量"显式声明：目标机更慢，统一一个大数会让超时的套件
# 既可能误报也可能白等，且失败信息必须能说清用的是哪一档。
SUITE_TIMEOUT = {
    'test_freeze_candidates.py': 1800,      # 内部重编译题卷
    'test_paper_r1_reproducible.py': 1800,  # 整卷逐字节重算
    'test_verify_offline.py': 1200,
    'test_chain_e2e.py': 1200,              # 起子进程跑完整链
}
DEFAULT_TIMEOUT = 900
def sha256(path):
    """文件内容指纹（凭据用它判新鲜度，不用 mtime）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


RESULT = re.compile(r'^Ran (\d+) tests?', re.M)
# 成行的结果标记（unittest 的 OK/OK (skipped=n)、自写套件的 ✅/PASS/全部通过）
SUCCESS_LINE = re.compile(r'^(?:OK(?: \(.*\))?|ALL PASS|PASS[: ]|✅|Ran \d+ tests)', re.M)
SKIP_LINE = re.compile(r'^(?:SKIP\b|⏭|跳过)')


def main(argv=None) -> int:
    argv = list(argv or [])
    json_out = None
    if '--json' in argv:                       # 不能让它落进 `only` 过滤器（实测会把全部套件筛空）
        i = argv.index('--json')
        json_out = argv[i + 1]
        del argv[i:i + 2]
    only = argv[0] if argv else None
    suites = sorted(list((ROOT / 'engine/tests').glob('test_*.py')) +
                    list((ROOT / 'tools/tests').glob('test_*.py')))
    if only:
        suites = [s for s in suites if only in s.name]
        if not suites:
            # 曾经"什么都没匹配到却返回 0"——测试跑器静默全绿比失败更危险
            print('没有套件匹配过滤器 %r；拒绝以全绿收场' % only, file=sys.stderr)
            return 2
    total_cases = 0
    bad_suites = []
    skipped_suites = 0
    skip_reason: dict = {}
    for path in suites:
        limit = SUITE_TIMEOUT.get(path.name, DEFAULT_TIMEOUT)
        try:
            proc = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                                  capture_output=True, text=True, timeout=limit,
                                  encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            bad_suites.append((path.name, 'TIMEOUT %ds（该套件声明值，可在 SUITE_TIMEOUT 调）' % limit, ''))
            print('FAIL  %-40s timeout' % path.name)
            continue
        blob = (proc.stderr or '') + (proc.stdout or '')
        ran = RESULT.search(blob)
        n = int(ran.group(1)) if ran else 0
        total_cases += n
        problems = [l for l in blob.splitlines() if BAD.match(l)]
        if 'FAILED (' in blob:                      # unittest 汇总行兜底
            hit = next((l for l in blob.splitlines() if l.startswith('FAILED ')), 'FAILED (…)')
            problems.append(hit)
        # 成功标记必须**成行**判定。上一版用 `'ok' in blob` 之类子串，任何路径里出现
        # "ok"（例如 /tmp/okb-delivery-xxx）都会把"其实没跑成"判成绿——静默全绿是最坏的失败模式。
        has_success = bool(SUCCESS_LINE.search(blob))
        has_skip = bool(SKIP_LINE.search(blob))
        clean = bool(has_success and not problems and proc.returncode == 0)
        if clean:
            print('PASS  %-40s cases=%d' % (path.relative_to(ROOT).as_posix(), n))
        elif (not problems and has_skip and not has_success and proc.returncode == 0):
            # 维护者产物不在树上的套件（build/ dist/ 未同步、无 node）必须算 SKIP：
            # 报 FAIL 会让人以为产品坏了（A 侧 10-08 回执里 7 条"失败"有 2 条其实是这种误判），
            # 报 PASS 是撒谎。SKIP 单独计数，写进凭据，README 也不得把它说成"全绿"。
            skipped_suites += 1
            skip_reason[path.name] = next((l for l in blob.splitlines() if SKIP_LINE.match(l.strip())), 'SKIP')[:110]
            print('SKIP  %-40s %s' % (path.relative_to(ROOT).as_posix(), skip_reason[path.name][:78]))
        else:
            bad_suites.append((path.name, problems[0][:80] if problems else 'no OK line',
                               blob.strip().splitlines()[-1][:120] if blob.strip() else ''))
            print('FAIL  %-40s %s' % (path.name, (problems[0] if problems else '无成功标记')[:70]))
    node = shutil.which('node')
    if node is None:
        js_ok, js_skipped = True, True
        print('SKIP  %-40s 原因：本机无 node（不算失败；有 node 的机器仍会真跑并要求 PASS）'
              % 'tools/tests/test_human_label.cjs')
    else:
        js_skipped = False
        js = subprocess.run([node, str(ROOT / 'tools/tests/test_human_label.cjs')],
                            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
                            encoding='utf-8', errors='replace')
        js_ok = js.returncode == 0 and 'PASS' in (js.stdout or '')
    if not js_skipped:                 # 跳过时上面已说明原因，不再伪造一行 PASS
        print('%s  %-40s' % ('PASS ' if js_ok else 'FAIL', 'tools/tests/test_human_label.cjs'))
    print()
    suites_n, cases_n, fails_n = len(suites) + 1, total_cases, len(bad_suites) + (0 if js_ok else 1)
    if js_skipped:
        skipped_suites += 1
    print('套件 %d 个，用例 %d 个，失败 %d 个，跳过 %d 个%s' % (
        suites_n, cases_n, fails_n, skipped_suites, '' if js_ok else '（含 JS）'))
    if skipped_suites:
        print('  SKIP 清单：%s' % '、'.join(sorted(skip_reason) or ['（JS 套件：本机无 node）']))
    if json_out:
        if True:
            out = pathlib.Path(json_out)
            newest = 0
            for d in ('tools/tests', 'engine/tests', 'tools', 'engine'):
                for f in (ROOT / d).rglob('*'):
                    if f.is_file() and f.suffix in ('.py', '.cjs'):
                        newest = max(newest, f.stat().st_mtime)
            # 新鲜度用**测试语料内容指纹**，不用 mtime：mtime 在换机器/clone/scp 之后必然刷新，
            # A 侧 VM 上那条"凭据比测试旧"就是这么假红的（文件内容一个字节都没变）。
            corpus = []
            for d in ('tools/tests', 'engine/tests'):
                for f in sorted((ROOT / d).rglob('*')):
                    if f.is_file() and f.suffix in ('.py', '.cjs') and '__pycache__' not in f.parts:
                        corpus.append('%s:%s' % (f.relative_to(ROOT).as_posix(), sha256(f)))
            corpus_sha = __import__('hashlib').sha256('\n'.join(corpus).encode('utf-8')).hexdigest()
            out.write_text(json.dumps(dict(
                format='okb-suite-status-v1', suites=suites_n, cases=cases_n, failures=fails_n,
                skipped_suites=skipped_suites, skip_reason=skip_reason,
                generated_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                .isoformat(timespec='seconds').replace('+00:00', 'Z'),
                newest_test_mtime=int(newest), js_skipped=js_skipped,
                test_corpus_sha256=corpus_sha, test_corpus_files=len(corpus),
                note='run_suite 的 --json 凭据；评审文档引用的套件/用例数必须与此一致。'
                     '新鲜度按 test_corpus_sha256 判（与机器无关）；mtime 只留作参考，不作判据'),
                ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
            print('已写计数凭据：' + str(out))
    for name, why, last in bad_suites:
        print('  FAIL %s :: %s :: %s' % (name, why, last))
    return 0 if not bad_suites and js_ok else 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
