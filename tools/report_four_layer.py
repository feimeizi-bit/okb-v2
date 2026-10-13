#!/usr/bin/env python3
"""Build a private, offline v2 diagnostic JSON/HTML report from one completed bundle.

Exit 0: report written (may contain unassessable tasks). Exit 2: rejected input.
The draft does not pool different runs or generate a formal score/ranking.
"""
import argparse
import pathlib as _pathlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT)]
from scoring.report_v2 import load_bundle, build_report, render_html, sha


def _attribution(plan_path, run_dir):
    """有基线族就计算归因；没有就如实返回 not_applicable。归因失败不得拖垮报告。"""
    import json as _json
    try:
        import baseline_attribution as BA
    except Exception as exc:                     # noqa: BLE001
        return dict(status='unavailable', reason='导入失败：%s: %s' % (type(exc).__name__, exc))
    try:
        plan = _json.loads(_pathlib.Path(plan_path).read_text(encoding='utf-8'))
    except Exception as exc:                     # noqa: BLE001
        return dict(status='unavailable', reason='计划读取失败：%s' % exc)
    try:
        rep = BA.analyse(_pathlib.Path(run_dir), plan)
    except SystemExit as exc:                    # 计划里没有 memory_before：不是错误
        return dict(status='not_applicable', reason=str(exc))
    except Exception as exc:                     # noqa: BLE001
        return dict(status='failed', reason='%s: %s' % (type(exc).__name__, exc))
    return dict(rep, status='computed')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('score-dir','run-dir','paper','plan','out-dir'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--verify-traces', action='store_true',
                   help='Offline current-projector reconstruction of checks, requests and citations')
    args=p.parse_args(argv)
    try:
        score,run,paper,plan,out=map(lambda s:Path(s).resolve(),(args.score_dir,args.run_dir,args.paper,args.plan,args.out_dir))
        if out.exists() or not out.parent.is_dir() or any(out.is_relative_to(d) for d in (score,run)):
            raise ValueError('output must be new and outside input bundles')
        bundle,snapshots,run_files=load_bundle(score,run,paper,plan,verify_trace=args.verify_traces)
        report=build_report(bundle, attribution=_attribution(plan, run))
        report['source_fingerprints']={str(path):sha(data) for path,data in snapshots.items()}
        rendered=render_html(report)
        if any(path.read_bytes()!=data for path,data in snapshots.items()) or {p.resolve() for p in run.rglob('*') if p.is_file()}!=run_files:
            raise ValueError('report inputs changed during read')
        out.mkdir()
        def write(name,value):
            with (out/name).open('x',encoding='utf8',newline='\n') as f:
                f.write(value)
        write('report.json',json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        write('index.html',rendered)
        write('complete.json',json.dumps({n:sha((out/n).read_bytes()) for n in ('report.json','index.html')},indent=2)+'\n')
        print(json.dumps(dict(report_directory=str(out),tasks=len(report['tasks']),review_tasks=len(report['review_tasks']),overall_score=None,release_authorized=False),ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('Diagnostic report rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
