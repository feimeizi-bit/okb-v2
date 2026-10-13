#!/usr/bin/env python3
"""Offline registered multi-run diagnostics. No execution, model calls or ranking.

Exit 0: diagnostic output completed, possibly with missing batches. Exit 2: rejected.
Paths in the imports JSON resolve relative to that JSON. Registration SHA is
required from a separately frozen record; this tool does not establish its age.
"""
import argparse
import html
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT)]
from scoring.multirun import load_comparison
from scoring.report_v2 import render_html, sha


def batch_attributions(imports_path, plan_path, run_dirs):
    """每个导入批次单独算归因；计划不含基线族时如实降级，绝不把缺证据说成 0 条目。"""
    import baseline_attribution as BA
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    out = []
    for rid, rdir in run_dirs:
        try:
            rep = BA.analyse(Path(rdir), plan)
            out.append(dict(rep, status='computed', run_id=rid))
        except SystemExit as exc:                       # 计划没有 memory_before：不是错误
            out.append(dict(status='not_applicable', run_id=rid, reason=str(exc)))
        except Exception as exc:                        # noqa: BLE001
            out.append(dict(status='failed', run_id=rid,
                            reason='%s: %s' % (type(exc).__name__, exc)))
    return out


def aggregate_reports(per_batch):
    import baseline_attribution as BA
    return BA.aggregate_reports(per_batch)


def render_comparison(report):
    esc=lambda v:html.escape(str(v),quote=True)
    pretty=lambda v:esc(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False))
    section='<h2>跨运行计划与导入范围</h2><p>计划批次：'+str(report['planned_batches'])+'；已导入：'+str(report['imported_batches'])
    section+='；主指标计划任务：'+str(report['planned_primary_tasks'])+'；全部尝试：'+str(report['planned_attempts'])+'</p>'
    section+='<p>缺失批次：'+esc(', '.join(report['missing_batches']) or '无')+'。主指标固定使用首次计划尝试；补跑不能覆盖首次失败。未导入任务保留在分母，记为不可判。</p>'
    section+='<details><summary>冻结配置、协议与注册指纹</summary><pre>'+pretty({k:report[k] for k in
        ('configuration_identities','protocol','registration_sha256','registration_precedence_independently_verified')})+'</pre></details>'
    section+='<details><summary>全部尝试与补跑历史（不增加独立样本数）</summary>'
    for i,t in enumerate(report['attempt_history']):
        section+=f'<details id="attempt-{i}"><summary>'+esc(t['attempt_id'])+' — '+esc(t['task_outcome'])+' / '+esc(t['import_status'])+'</summary><pre>'+pretty(t)+'</pre></details>'
    section+='</details>'
    return render_html(report).replace('<h2>六维覆盖与结果</h2>',section+'<h2>六维覆盖与结果</h2>',1)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('registration','registration-sha256','imports','paper','plan','out-dir'):
        p.add_argument('--'+name,required=True)
    args=p.parse_args(argv)
    try:
        out=Path(args.out_dir).resolve()
        if out.exists() or not out.parent.is_dir():
            raise ValueError('output must be new with existing parent')
        report,snapshots,run_files,source_dirs=load_comparison(
            args.registration,args.registration_sha256,args.imports,args.paper,args.plan)
        if any(out.is_relative_to(d) for d in source_dirs) or any(p.is_relative_to(out) for p in snapshots):
            raise ValueError('output overlaps inputs')
        # 跨轮仪器自证：每轮单独算，再聚合出"残留是否随轮递增"
        run_dirs = []
        imps = json.loads(Path(args.imports).resolve().read_text(encoding='utf-8'))
        for entry in imps.get('batches', []):
            rid = entry.get('run_id')
            rdir = (Path(args.imports).resolve().parent / entry.get('run_dir', '')).resolve()
            if rid and rdir.is_dir():
                run_dirs.append((rid, rdir))
        report['attribution'] = aggregate_reports(batch_attributions(args.imports, args.plan, run_dirs))
        report['attribution']['imported_batches'] = len(run_dirs)
        rendered=render_comparison(report)
        if any(p.read_bytes()!=data for p,data in snapshots.items()) or any(
                {p.resolve() for p in run.rglob('*') if p.is_file()}!=files for run,files in run_files.items()):
            raise ValueError('comparison inputs changed during read')
        out.mkdir()
        for name,content in [('report.json',json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'),('index.html',rendered)]:
            with (out/name).open('x',encoding='utf8',newline='\n') as f:f.write(content)
        with (out/'complete.json').open('x',encoding='utf8',newline='\n') as f:
            json.dump({n:sha((out/n).read_bytes()) for n in ('report.json','index.html')},f,indent=2)
            f.write('\n')
        print(json.dumps(dict(report_directory=str(out),planned_batches=report['planned_batches'],
            imported_batches=report['imported_batches'],missing_batches=report['missing_batches'],
            overall_score=None,release_authorized=False),ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        # Input strings are private; use stable rejection category without dumping them.
        print('Multi-run report rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
