#!/usr/bin/env python3
"""okb v2 六维雷达渲染器：缺失维度显式标注，多 run 汇总带误差范围。

    python3 tools/scoring/radar_v2.py --in build/report-A.json --in build/report-B.json \
        --out-svg build/radar-v2.svg [--out-json build/radar-v2.json] \
        [--value assessed_task_pass_rate|complete_sample_mean|sample_coverage|common_unit_pass_rate] \
        [--error range|sd|none] [--title 文本]

零依赖：纯 stdlib，SVG 用字符串手写（沿用 tools/radar.py 的先例）。
只消费 tools/scoring/report_v2.py 的诊断报告结构（dimensions[dim]["agents"][agent]
的 metric 字典，以及 dimensions[dim]["common_unit_pass_rates"]）：绘图层不重算
分数、不出总分、不做排名。

与旧 tools/radar.py 的区别（旧文件按既有口径保持不动）：
  * 缺失（None / 全部 unassessable / insufficient / 该维无计划任务 / 报告里没有该维或该 agent）
    保留 null，并在轴上画空心方块 + 标注「缺失/insufficient」；绝不折算成 0，
    也不作为多边形顶点（真 0 分照常画在圆心方向，二者可区分）；
  * 多 run 输入按维度聚合 mean/min/max/range/sd，误差带只在 n>=2 时绘制，n=1 明确标注；
  * 每个 agent 附各维度的 run 数、有值 run 数、覆盖率与配置标识。
探索性概览图，不是正式总榜：正式六维分数与排名口径仍由报告层与 §7 门槛决定。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

FORMAT = 'okb-radar-v2-draft'
GENERATED_BY = 'tools/scoring/radar_v2.py'
DIMS = ('long_term_retention', 'memory_recall', 'dynamic_update',
        'similar_discrimination', 'boundary_recognition', 'task_reuse')
ZH = {'long_term_retention': '长期保持', 'memory_recall': '记忆调用',
      'dynamic_update': '动态更新', 'similar_discrimination': '相近区分',
      'boundary_recognition': '边界识别', 'task_reuse': '任务复用'}
MISSING_LABEL = '缺失/insufficient'
PALETTE = ('#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf')
VALUE_KEYS = ('assessed_task_pass_rate', 'complete_sample_mean',
              'sample_coverage', 'common_unit_pass_rate')
ERROR_BANDS = ('range', 'sd', 'none')
LAYOUT = {'width': 1360, 'height': 730, 'cx': 420, 'cy': 352, 'radius': 208, 'legend_x': 830}
MISSING_POLICY = ('null 保持 null：缺失维度标注 %s，不折算成 0，也不作为多边形顶点；'
                  '聚合只用有值的 run，真 0 分照常画在轴心方向' % MISSING_LABEL)


class RadarError(ValueError):
    """Invalid or non-v2 report input for the radar layer."""


# ----------------------------------------------------------------聚合层（只读报告结构）

def _reports(report):
    if isinstance(report, dict):
        return [report]
    if isinstance(report, (list, tuple)) and report and all(isinstance(r, dict) for r in report):
        return list(report)
    raise RadarError('report must be one v2 report dict or a non-empty sequence of them')


def _unit(value, where):
    if value is None:
        return None
    if isinstance(value, bool) or type(value) not in (int, float):
        raise RadarError('%s must be a 0-1 rate or null, got %r' % (where, value))
    v = float(value)
    if not math.isfinite(v) or not 0.0 <= v <= 1.0:
        raise RadarError('%s is outside the 0-1 range: %r' % (where, value))
    return v


def _dimension(report, dim):
    dims = report.get('dimensions')
    if not isinstance(dims, dict):
        raise RadarError('report has no dimensions mapping (needs okb v2 diagnostic report)')
    if dim not in dims:
        return None
    node = dims[dim]
    if node is None:
        return None
    if not isinstance(node, dict):
        raise RadarError('dimension %s is not a mapping' % dim)
    return node


def _metric(report, dim, agent):
    node = _dimension(report, dim)
    if node is None:
        return None
    agents = node.get('agents')
    if not isinstance(agents, dict) or agent not in agents:
        return None
    metric = agents[agent]
    if not isinstance(metric, dict):
        raise RadarError('dimension %s agent %s metric is not a mapping' % (dim, agent))
    return metric


def _missing_reason(value_key, metric):
    planned = metric.get('planned_tasks')
    assessed = (metric.get('pass') or 0) + (metric.get('fail') or 0)
    if not planned:
        return 'no planned task in this dimension'
    if value_key == 'complete_sample_mean':
        if not metric.get('fully_assessable_samples'):
            return 'no fully assessable sample (unassessable/insufficient)'
    elif value_key != 'sample_coverage':
        if not assessed:
            return 'no assessable task (all unassessable/insufficient)'
    if metric.get('unassessable'):
        return 'assessable subset empty (unassessable/insufficient present)'
    return 'null value in v2 report metric'


def _observation(report, dim, agent, value_key, index):
    run_id = report.get('run_id') or 'run%d' % (index + 1)
    node = _dimension(report, dim)
    if node is None:
        return dict(run_id=run_id, value=None, status='missing',
                    reason='dimension absent from report')
    if value_key == 'common_unit_pass_rate':
        rates = node.get('common_unit_pass_rates')
        if not isinstance(rates, dict) or agent not in rates:
            return dict(run_id=run_id, value=None, status='missing',
                        reason='agent absent from common_unit_pass_rates')
        raw = rates[agent]
        if raw is None:
            return dict(run_id=run_id, value=None, status='missing',
                        reason='no common assessable unit for this agent')
        return dict(run_id=run_id, value=_unit(raw, '%s.%s common rate' % (dim, agent)), status='observed')
    metric = _metric(report, dim, agent)
    if metric is None:
        return dict(run_id=run_id, value=None, status='missing', reason='agent absent from report')
    raw = metric.get(value_key)
    if raw is None:
        return dict(run_id=run_id, value=None, status='missing',
                    reason=_missing_reason(value_key, metric))
    return dict(run_id=run_id, value=_unit(raw, '%s.%s %s' % (dim, agent, value_key)),
                status='observed', planned_tasks=metric.get('planned_tasks'),
                planned_samples=metric.get('planned_samples'), passed=metric.get('pass'),
                failed=metric.get('fail'), unassessable=metric.get('unassessable'),
                sample_coverage=_unit(metric.get('sample_coverage'), '%s.%s coverage' % (dim, agent)),
                unresolved_classification=metric.get('unresolved_classification'))


def _aggregate(values, error_band):
    present = [v for v in values if v is not None]
    n = len(present)
    out = dict(n_runs=len(values), n_values=n, n_missing=len(values) - n)
    if not n:
        out.update(mean=None, min=None, max=None, range=None, sd=None, error_band=None,
                   error=None, band_low=None, band_high=None,
                   note='all %d run(s) missing this dimension; nothing is plotted as 0' % len(values))
        return out
    mean = sum(present) / n
    lo, hi = min(present), max(present)
    out.update(mean=mean, min=lo, max=hi, range=hi - lo,
               sd=math.sqrt(sum((x - mean) ** 2 for x in present) / (n - 1)) if n >= 2 else None)
    if n < 2:
        out.update(error_band=None, error=None, band_low=None, band_high=None,
                   note='n=1：单 run 数值，不画误差带')
    elif error_band == 'none':
        out.update(error_band=None, error=None, band_low=None, band_high=None,
                   note='误差带已关闭（--error none）')
    elif error_band == 'sd':
        err = out['sd']
        out.update(error_band='sd', error=err, band_low=max(0.0, mean - err),
                   band_high=min(1.0, mean + err), note='样本 SD（ddof=1），绘图按 [0,1] 截断')
    else:
        err = out['range']
        out.update(error_band='range', error=err, band_low=max(0.0, mean - err),
                   band_high=min(1.0, mean + err), note='极差 max-min，绘图按 [0,1] 截断')
    return out


def _agent_names(report):
    names = []
    declared = report.get('agents')
    if isinstance(declared, list):
        for entry in declared:
            name = entry.get('name') if isinstance(entry, dict) else entry
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    for dim in DIMS:
        node = _dimension(report, dim) or {}
        agents = node.get('agents')
        if isinstance(agents, dict):
            names.extend(a for a in agents if a not in names)
        rates = node.get('common_unit_pass_rates')
        if isinstance(rates, dict):
            names.extend(a for a in rates if a not in names)
    return names


def _identity(reports, agent):
    identity = {}
    for report in reports:
        for entry in report.get('agents') or []:
            if isinstance(entry, dict) and entry.get('name') == agent:
                for key in ('agent_kind', 'model', 'temperature'):
                    if entry.get(key) is not None:
                        identity.setdefault(key, []).append(entry[key])
    out = {}
    for key, seen in identity.items():
        unique = []
        for value in seen:
            if not any(value == held and type(value) is type(held) for held in unique):
                unique.append(value)
        out[key] = unique[0] if len(unique) == 1 else unique
    return out


def summarize(report, *, value_key='assessed_task_pass_rate', error_band='range'):
    """Aggregate v2 report dimension metrics per agent, keeping missing as null."""
    if value_key not in VALUE_KEYS:
        raise RadarError('value must be one of %s' % (', '.join(VALUE_KEYS),))
    if error_band not in ERROR_BANDS:
        raise RadarError('error must be one of %s' % (', '.join(ERROR_BANDS),))
    reports = _reports(report)
    run_ids = [(r.get('run_id') or 'run%d' % (i + 1)) for i, r in enumerate(reports)]
    seen = set()
    for rid in run_ids:
        if rid in seen:
            raise RadarError('duplicate run_id %s: one report per run' % rid)
        seen.add(rid)
    agents = []
    for report in reports:
        for name in _agent_names(report):
            if name not in agents:
                agents.append(name)
    if not agents:
        raise RadarError('report declares no agent')

    payload = dict(format=FORMAT, generated_by=GENERATED_BY, value_key=value_key,
                   error_band=error_band, missing_policy=MISSING_POLICY,
                   dimensions=[dict(key=dim, label_zh=ZH[dim]) for dim in DIMS],
                   source_runs=run_ids,
                   source_formats=[r.get('format') for r in reports],
                   source_modes=[r.get('mode') for r in reports],
                   source_evidence_policies=[r.get('evidence_policy') for r in reports],
                   source_plan_sha256=sorted({str(r.get('plan_sha256')) for r in reports}),
                   overall_score=None, ranking=None, agents=[],
                   limits=['探索性概览：本图不含正式六维总分或排名。',
                           'Missing and unresolved values remain null, not zero.',
                           '误差带只是多 run 观测离散度的描述，不是置信区间，也不构成显著性结论。',
                           '重复任务不增加独立样本数；覆盖率与 n 见各维度明细。'])
    plans = payload['source_plan_sha256']
    if len(plans) > 1:
        payload['limits'].append('输入 run 使用了 %d 个不同 plan 指纹：跨 run 比较未被确认。' % len(plans))

    for agent in agents:
        series = []
        for dim in DIMS:
            values = [_observation(r, dim, agent, value_key, i) for i, r in enumerate(reports)]
            stats = _aggregate([v['value'] for v in values], error_band)
            stats.update(dimension=dim, label_zh=ZH[dim],
                         status='missing' if stats['n_values'] == 0 else 'plotted',
                         missing_runs=[v['run_id'] for v in values if v['value'] is None],
                         missing_reason=sorted({v['reason'] for v in values if v['value'] is None}),
                         values=values)
            series.append(stats)
        entry = dict(name=agent)
        entry.update(_identity(reports, agent))
        entry.update(dimensions=series,
                     missing_dimensions=[s['dimension'] for s in series if s['status'] == 'missing'])
        payload['agents'].append(entry)
    payload['all_missing'] = all(bool(a['missing_dimensions']) and
                                 len(a['missing_dimensions']) == len(DIMS) for a in payload['agents'])
    return payload


# ------------------------------------------------------------------绘图层（只消费聚合结果）

def _pt(cx, cy, r, k, total=len(DIMS)):
    ang = -math.pi / 2 + 2 * math.pi * k / total
    return cx + r * math.cos(ang), cy + r * math.sin(ang)


def _text(x, y, size, content, fill='#333', anchor='start', weight=None, opacity=None):
    attrs = 'x="%.1f" y="%.1f" font-size="%d" text-anchor="%s" fill="%s"' % (x, y, size, anchor, fill)
    if weight:
        attrs += ' font-weight="%s"' % weight
    if opacity is not None:
        attrs += ' fill-opacity="%s"' % opacity
    return '<text %s>%s</text>' % (attrs, _esc(content))


def _esc(value):
    return (str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def draw_svg(payload, *, title):
    """Hand-written SVG: observed vertices only, missing axes annotated, optional error band."""
    W, H = LAYOUT['width'], LAYOUT['height']
    CX, CY, R = LAYOUT['cx'], LAYOUT['cy'], LAYOUT['radius']
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
           'font-family="system-ui,sans-serif">' % (W, H, W, H),
           '<rect width="%d" height="%d" fill="white"/>' % (W, H),
           _text(24, 34, 18, title, fill='#20252b', weight='bold'),
           _text(24, 56, 12, '探索性诊断 · 无正式总分/排名 · 指标 %s · 误差带 %s'
                 % (payload['value_key'], payload['error_band']), fill='#5a6270')]
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = ' '.join('%.1f,%.1f' % _pt(CX, CY, R * ring, k) for k in range(len(DIMS)))
        out.append('<polygon points="%s" fill="none" stroke="#cfd4da" stroke-width="1"/>' % pts)
        # 刻度放在纵轴左侧，避免与顶部维度的缺失标注重叠。
        out.append(_text(CX - 8, CY - R * ring + 4, 10, '%d%%' % int(ring * 100),
                         fill='#8a919b', anchor='end'))
    for k, dim in enumerate(DIMS):
        x2, y2 = _pt(CX, CY, R, k)
        out.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" stroke="#cfd4da" stroke-width="1"/>'
                   % (CX, CY, x2, y2))
        lx, ly = _pt(CX, CY, R + 30, k)
        anchor = 'middle' if abs(lx - CX) < 12 else ('start' if lx > CX else 'end')
        out.append(_text(lx, ly, 13, ZH[dim], anchor=anchor, weight='bold'))
        out.append(_text(lx, ly + 14, 9, dim, fill='#8a919b', anchor=anchor))

    for i, agent in enumerate(payload['agents']):
        color = PALETTE[i % len(PALETTE)]
        series = {s['dimension']: s for s in agent['dimensions']}
        observed = [(k, series[dim]) for k, dim in enumerate(DIMS) if series[dim]['n_values']]
        out.append('<!-- agent=%s observed=%s -->' % (agent['name'],
                   ','.join(s['dimension'] for _, s in observed) or '-'))
        for k, stats in observed:
            mean = stats['mean']
            x, y = _pt(CX, CY, R * mean, k)
            label = ('%s / %s：%s=%d run 有值 %.4f（n=%d，min %.4f，max %.4f，%s）'
                     % (agent['name'], stats['dimension'], payload['value_key'], stats['n_values'],
                        mean, stats['n_values'], stats['min'], stats['max'], stats['note']))
            if stats['error'] is not None:
                inner = _pt(CX, CY, R * stats['band_low'], k)
                outer = _pt(CX, CY, R * stats['band_high'], k)
                out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                           'stroke-width="5" stroke-opacity="0.22" stroke-linecap="round">'
                           '<title>%s</title></line>' % (inner[0], inner[1], outer[0], outer[1],
                                                          color, _esc(label)))
                for (wx, wy) in (inner, outer):
                    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                               'stroke-width="1.5" stroke-opacity="0.65"/>'
                               % (wx - 4, wy, wx + 4, wy, color))
            shape = '<circle cx="%.1f" cy="%.1f" r="3.5" fill="%s"><title>%s</title></circle>' % (
                x, y, color, _esc(label))
            out.append(shape)
            if stats['n_values'] == 1:
                tx, ty = _pt(CX, CY, R * mean + 13, k)
                out.append(_text(tx, ty + 3, 9, 'n=1', fill=color, anchor='middle'))
        vertices = [_pt(CX, CY, R * stats['mean'], k) for k, stats in observed]
        if len(vertices) >= 3:
            pts = ' '.join('%.1f,%.1f' % v for v in vertices)
            out.append('<!-- polygon agent=%s vertices=%d -->' % (agent['name'], len(vertices)))
            out.append('<polygon points="%s" fill="%s" fill-opacity="0.10" stroke="%s" '
                       'stroke-width="2" stroke-linejoin="round"/>' % (pts, color, color))
        elif len(vertices) == 2:
            (x1, y1), (x2, y2) = vertices
            out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" fill="none" stroke="%s" '
                       'stroke-width="2"/>' % (x1, y1, x2, y2, color))
        for k, dim in enumerate(DIMS):
            stats = series[dim]
            if stats['n_values']:
                continue
            mx, my = _pt(CX, CY, R - 12 - 15 * i, k)
            reason = '；'.join(stats['missing_reason']) or '缺失'
            tip = '%s / %s：%s（%s run 全部缺失）' % (agent['name'], dim, MISSING_LABEL, stats['n_runs'])
            out.append('<rect x="%.1f" y="%.1f" width="9" height="9" fill="none" stroke="%s" '
                       'stroke-width="1.5" stroke-dasharray="3,2"><title>%s</title></rect>'
                       % (mx - 4.5, my - 4.5, color, _esc(tip + '｜' + reason)))
            lx, ly = _pt(CX, CY, R + 46, k)
            anchor = 'middle' if abs(lx - CX) < 12 else ('start' if lx > CX else 'end')
            out.append(_text(lx, ly + 42 + 13 * i, 10, '%s：%s' % (agent['name'], MISSING_LABEL),
                             fill=color, anchor=anchor))

    LX = LAYOUT['legend_x']
    top = 82
    out.append(_text(LX, top, 13, 'Agent（配置标识）', fill='#20252b', weight='bold'))
    for i, agent in enumerate(payload['agents']):
        color = PALETTE[i % len(PALETTE)]
        y = top + 24 + 60 * i
        out.append('<rect x="%d" y="%d" width="13" height="13" fill="%s" fill-opacity="0.55"/>'
                   % (LX, y - 11, color))
        out.append(_text(LX + 20, y, 13, agent['name'], fill='#20252b', weight='bold'))
        detail = ' / '.join('%s=%s' % (k, agent.get(k, '未披露')) for k in
                            ('agent_kind', 'model', 'temperature'))
        out.append(_text(LX + 20, y + 15, 10, detail, fill='#5a6270'))
        counts = '、'.join('%s n=%d/%d' % (s['label_zh'], s['n_values'], s['n_runs'])
                           for s in agent['dimensions'])
        out.append(_text(LX + 20, y + 29, 9, counts, fill='#5a6270'))
        if agent['missing_dimensions']:
            out.append(_text(LX + 20, y + 43, 9, '%s：%s' % (MISSING_LABEL,
                         '、'.join(ZH[d] for d in agent['missing_dimensions'])), fill=color))
        else:
            partial = [s for s in agent['dimensions'] if s['n_missing']]
            out.append(_text(LX + 20, y + 43, 9, '六维均有值' + ('（部分 run 缺失：%s）'
                         % '、'.join(ZH[s['dimension']] for s in partial) if partial else ''),
                         fill='#5a6270'))
    note = top + 24 + 60 * len(payload['agents']) + 10
    lines = ['空心方块+%s：该维度无有效判定，不折算成 0，也不作为多边形顶点' % MISSING_LABEL,
             '聚合 mean/min/max/range/sd 只用有值的 run；真 0 分照常画在轴心方向',
             '粗短带=误差范围（%s），按 [0,1] 截断' % ('极差 max-min' if payload['error_band'] == 'range'
              else '样本 SD（ddof=1）' if payload['error_band'] == 'sd' else '已关闭')]
    if any(s['n_values'] == 1 for a in payload['agents'] for s in a['dimensions']):
        lines.append('误差带仅在 n>=2 绘制；n=1 只标 n=1')
    lines.append('run：%s' % '、'.join(payload['source_runs']))
    for line in lines:
        out.append(_text(LX, note, 10, line, fill='#5a6270'))
        note += 14
    return '\n'.join(out) + '\n</svg>\n'


# -------------------------------------------------------------------------对外 API / CLI

def render(report, *, out_svg, out_json=None, value_key='assessed_task_pass_rate',
           error_band='range', title=None):
    """Render one v2 report, or a list of per-run v2 reports, to SVG plus optional JSON.

    Returns the aggregated payload dict. Missing dimensions stay null and are annotated
    「缺失/insufficient」 on their axis instead of being drawn as 0; per dimension the
    payload carries mean/min/max/range/sd over the runs that had a value, the run-by-run
    values (nulls included) and the coverage counts taken from the report metric.
    """
    payload = summarize(report, value_key=value_key, error_band=error_band)
    out_svg = Path(out_svg)
    svg = draw_svg(payload, title=title or 'okb 六维雷达（缺失不填 0）')
    _write(out_svg, svg)
    payload['svg'] = str(out_svg)
    if out_json is not None:
        out_json = Path(out_json)
        _write(out_json, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False,
                                    sort_keys=False) + '\n')
        payload['json'] = str(out_json)
    return payload


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + '.tmp%d' % os.getpid())
    with tmp.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description='okb v2 六维雷达（缺失=标注，不填 0；多 run 带误差带）')
    ap.add_argument('--in', dest='inputs', action='append', required=True,
                    help='v2 诊断报告 report.json，可重复传入实现多 run 汇总')
    ap.add_argument('--out-svg', required=True)
    ap.add_argument('--out-json', help='默认与 SVG 同名 .json；传 --no-json 则不写')
    ap.add_argument('--no-json', action='store_true')
    ap.add_argument('--value', default='assessed_task_pass_rate', choices=VALUE_KEYS)
    ap.add_argument('--error', default='range', choices=ERROR_BANDS)
    ap.add_argument('--title')
    args = ap.parse_args(argv)
    try:
        reports = [json.loads(Path(p).read_text(encoding='utf-8')) for p in args.inputs]
        out_json = None if args.no_json else Path(args.out_json or
                                                  Path(args.out_svg).with_suffix('.json'))
        payload = render(reports, out_svg=args.out_svg, out_json=out_json,
                         value_key=args.value, error_band=args.error, title=args.title)
    except (RadarError, ValueError, OSError, KeyError, TypeError) as exc:
        print('radar_v2 rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    missing = {a['name']: len(a['missing_dimensions']) for a in payload['agents']}
    print(json.dumps(dict(svg=payload['svg'], json=payload.get('json'),
                          runs=payload['source_runs'], agents=missing,
                          value_key=payload['value_key'], error_band=payload['error_band'],
                          all_missing=payload['all_missing'], overall_score=None,
                          ranking=None), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
