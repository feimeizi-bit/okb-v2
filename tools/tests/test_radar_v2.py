#!/usr/bin/env python3
"""v2 雷达：缺失维度必须标注而非填 0；多 run 聚合给出 mean/min/max/range/sd 与误差带。

    python3 tools/tests/test_radar_v2.py

离线、零第三方依赖；报告数据由真实生产者 scoring.report_v2.build_report 构造，
以验证 radar_v2 消费的是 v2 报告结构而不是自造结构。
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(Path(__file__).parent)]

from scoring import radar_v2
from scoring.radar_v2 import (DIMS, LAYOUT, MISSING_LABEL, RadarError, _pt, draw_svg,
                              main, render, summarize)
from scoring.report_v2 import DIMS as REPORT_DIMS, build_report

AGENTS = ('openclaw', 'hermes')
OUTCOMES = ('pass', 'fail', 'unassessable')


def tasks_for(agent, dim, outcomes):
    rows = []
    for index, outcome in enumerate(outcomes, start=1):
        score = {'pass': 1, 'fail': 0}.get(outcome)
        rows.append(dict(task_id='%s-%s-r%d' % (agent, dim, index), agent=agent,
                         sample_id='%s_%d' % (dim, index), dimension=dim,
                         repeat=str(index), task_outcome=outcome, score=score,
                         error_class='remembered' if outcome == 'pass' else None,
                         classification_status='unresolved' if outcome == 'unassessable' else 'resolved',
                         comparison='same', layer1=dict(valid=True), checks=[], evidence_refs=[]))
    return rows


def report(run_id, cells, plan_sha='a' * 64):
    """cells: {agent: {dimension: [outcomes] 或 None}} -> 真实 v2 诊断报告 dict。

    None 表示该 agent 在该维度没有任何计划任务；样本 id 跨 agent 共享，
    与真实报告一致（共同可判子集才有非空 common_unit_pass_rates）。
    """
    tasks, samples = [], {}
    for agent, dims in cells.items():
        for dim, outcomes in dims.items():
            if outcomes is None:
                continue
            rows = tasks_for(agent, dim, outcomes)
            tasks.extend(rows)
            for row in rows:
                samples[row['sample_id']] = dict(id=row['sample_id'], dimension=dim)
    bundle = dict(tasks=tasks,
                  agents={a: dict(agent_kind='real' if a == AGENTS[0] else 'mock',
                                  model='m-' + a, temperature=0.2 if a == AGENTS[0] else 0)
                          for a in cells},
                  selected={sid: dict(sample_id=sid) for sid in samples}, samples=samples,
                  run_id=run_id, summary=dict(mode='replay', evidence_policy='scoped-v1'),
                  lock=dict(plan_sha256=plan_sha))
    return build_report(bundle)


def full_cells(**overrides):
    """每维 2 题通过率 0.5；overrides 形如 {'openclaw.task_reuse': ['pass'] 或 None}。"""
    cells = {agent: {dim: ['pass', 'fail'] for dim in DIMS} for agent in AGENTS}
    for key, value in overrides.items():
        agent, dim = key.split('.', 1)
        cells[agent][dim] = value
    return cells


def series(payload, agent, dim):
    entry = next(a for a in payload['agents'] if a['name'] == agent)
    return next(s for s in entry['dimensions'] if s['dimension'] == dim)


def agent_marker(svg, agent):
    for line in svg.splitlines():
        if line.startswith('<!-- agent=%s ' % agent):
            return line.split('observed=')[1].rstrip(' ->')
    raise AssertionError('agent marker missing for %s' % agent)


class RadarV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    # ---------------------------------------------------------------- 维度键名对齐
    def test_dimensions_align_with_report_v2(self):
        self.assertEqual(DIMS, REPORT_DIMS)
        self.assertEqual(set(DIMS), set(radar_v2.ZH))
        self.assertEqual(sorted(radar_v2.ZH.values()),
                         sorted(['任务复用', '动态更新', '记忆调用', '相近区分', '边界识别', '长期保持']))

    # ---------------------------------------------------------------- 缺失不填 0
    def test_unassessable_dimension_is_annotated_not_plotted(self):
        payload = render(report('r1', full_cells(**{
            'openclaw.dynamic_update': ['unassessable', 'unassessable']})),
            out_svg=self.root / 'a.svg', out_json=self.root / 'a.json')
        stats = series(payload, 'openclaw', 'dynamic_update')
        self.assertEqual(stats['status'], 'missing')
        self.assertIsNone(stats['mean'])
        self.assertIsNone(stats['min'])
        self.assertIsNone(stats['sd'])
        self.assertEqual(stats['values'][0]['value'], None)
        self.assertEqual(stats['missing_reason'], ['no assessable task (all unassessable/insufficient)'])
        svg = (self.root / 'a.svg').read_text(encoding='utf-8')
        # 缺失维度不出现在多边形顶点里，其余 5 维照常出图。
        self.assertEqual(agent_marker(svg, 'openclaw'),
                         ','.join(d for d in DIMS if d != 'dynamic_update'))
        self.assertIn('polygon agent=openclaw vertices=5', svg)
        self.assertIn('缺失/insufficient', svg)
        self.assertIn('openclaw：缺失/insufficient', svg)
        self.assertIn('stroke-dasharray', svg)
        # JSON 里是 null，而不是 0。
        doc = json.loads((self.root / 'a.json').read_text(encoding='utf-8'))
        raw = json.dumps(doc, ensure_ascii=False)
        self.assertIn('"mean": null', raw)
        raw_dim = next(s for s in next(a for a in doc['agents'] if a['name'] == 'openclaw')['dimensions']
                       if s['dimension'] == 'dynamic_update')
        self.assertIsNone(raw_dim['mean'])
        self.assertIsNone(raw_dim['values'][0]['value'])

    def test_planned_task_absence_is_also_missing(self):
        payload = summarize(report('r1', full_cells(**{'hermes.task_reuse': None})))
        stats = series(payload, 'hermes', 'task_reuse')
        self.assertEqual(stats['status'], 'missing')
        self.assertIsNone(stats['mean'])
        self.assertEqual(stats['missing_reason'], ['no planned task in this dimension'])

    def test_genuine_zero_is_plotted_and_differs_from_missing(self):
        payload = render(report('r1', full_cells(**{
            'openclaw.memory_recall': ['fail', 'fail'],
            'openclaw.task_reuse': ['unassessable', 'unassessable']})),
            out_svg=self.root / 'z.svg')
        zero = series(payload, 'openclaw', 'memory_recall')
        self.assertEqual(zero['status'], 'plotted')
        self.assertEqual(zero['mean'], 0.0)
        missing = series(payload, 'openclaw', 'task_reuse')
        self.assertEqual(missing['status'], 'missing')
        self.assertIsNone(missing['mean'])
        svg = (self.root / 'z.svg').read_text(encoding='utf-8')
        center = '%.1f,%.1f' % _pt(LAYOUT['cx'], LAYOUT['cy'], 0.0, DIMS.index('memory_recall'))
        polygon = [line for line in svg.splitlines() if 'polygon agent=openclaw' in line][0]
        points = svg.split('%s\n' % polygon)[1].split('points="')[1].split('"')[0]
        self.assertIn(center, points)  # 真 0 分画在轴心；缺失则根本没有这个顶点

    # ---------------------------------------------------------------- 归一化与校验
    def test_values_are_normalized_onto_unit_radius(self):
        payload = render(report('r1', full_cells(**{'openclaw.boundary_recognition': ['pass', 'pass']})),
                         out_svg=self.root / 'n.svg')
        stats = series(payload, 'openclaw', 'boundary_recognition')
        self.assertEqual(stats['mean'], 1.0)
        axis = DIMS.index('boundary_recognition')
        want = '%.1f,%.1f' % _pt(LAYOUT['cx'], LAYOUT['cy'], LAYOUT['radius'] * stats['mean'], axis)
        svg = (self.root / 'n.svg').read_text(encoding='utf-8')
        self.assertIn(want, svg)
        for entry in payload['agents']:
            for dim in entry['dimensions']:
                if dim['mean'] is not None:
                    self.assertGreaterEqual(dim['mean'], 0.0)
                    self.assertLessEqual(dim['mean'], 1.0)

    def test_out_of_range_and_bad_input_rejected(self):
        bad = report('r1', full_cells())
        bad['dimensions']['task_reuse']['agents']['openclaw']['assessed_task_pass_rate'] = 1.5
        with self.assertRaises(RadarError):
            summarize(bad)
        with self.assertRaises(RadarError):
            summarize({'format': 'okb-diagnostic-report-v2-draft'})
        with self.assertRaises(RadarError):
            summarize([])
        with self.assertRaises(RadarError):
            summarize(report('r1', full_cells()), value_key='invented_metric')
        with self.assertRaises(RadarError):
            summarize(report('r1', full_cells()), error_band='ci')
        with self.assertRaises(RadarError):
            summarize([report('same', full_cells()), report('same', full_cells())])

    # ---------------------------------------------------------------- 多 run 聚合
    def test_multi_run_reports_give_mean_min_max_range_sd(self):
        runs = [report('run-%d' % i, full_cells(**{('openclaw.long_term_retention'): o}))
                for i, o in enumerate([['pass'] * 4, ['fail'] * 4, ['pass'] * 2 + ['fail'] * 2], start=1)]
        payload = render(runs, out_svg=self.root / 'm.svg', out_json=self.root / 'm.json')
        stats = series(payload, 'openclaw', 'long_term_retention')
        self.assertEqual(stats['n_runs'], 3)
        self.assertEqual(stats['n_values'], 3)
        self.assertEqual([v['value'] for v in stats['values']], [1.0, 0.0, 0.5])
        self.assertEqual(stats['mean'], 0.5)
        self.assertEqual((stats['min'], stats['max']), (0.0, 1.0))
        self.assertEqual(stats['range'], 1.0)
        self.assertAlmostEqual(stats['sd'], 0.5, places=6)
        self.assertEqual(stats['error_band'], 'range')
        self.assertEqual(stats['error'], 1.0)
        self.assertEqual((stats['band_low'], stats['band_high']), (0.0, 1.0))
        svg = (self.root / 'm.svg').read_text(encoding='utf-8')
        expected = sum(1 for entry in payload['agents'] for one in entry['dimensions']
                       if one['error'] is not None)
        self.assertEqual(expected, 2 * len(DIMS))  # 两个 agent 六维都有 n=3
        self.assertEqual(svg.count('stroke-opacity="0.22"'), expected)  # 每维一条误差带
        ET.parse(self.root / 'm.svg')

    def test_sd_band_and_missing_runs_excluded_from_mean(self):
        runs = [report('run-1', full_cells()),
                report('run-2', full_cells(**{'openclaw.task_reuse': ['unassessable']})),
                report('run-3', full_cells(**{'openclaw.task_reuse': ['pass', 'pass', 'fail']}))]
        payload = summarize(runs, error_band='sd')
        stats = series(payload, 'openclaw', 'task_reuse')
        self.assertEqual(stats['n_runs'], 3)
        self.assertEqual(stats['n_values'], 2)
        self.assertEqual(stats['missing_runs'], ['run-2'])
        self.assertEqual([v['value'] for v in stats['values']], [0.5, None, 2 / 3])
        self.assertAlmostEqual(stats['mean'], (0.5 + 2 / 3) / 2)
        self.assertEqual(stats['min'], 0.5)
        self.assertEqual(stats['max'], 2 / 3)
        self.assertEqual(stats['error_band'], 'sd')
        self.assertAlmostEqual(stats['error'], stats['sd'])
        # 缺失那轮绝不以 0 参与均值。
        self.assertNotAlmostEqual(stats['mean'], (0.5 + 0.0 + 2 / 3) / 3)

    def test_single_run_marks_n1_and_draws_no_band(self):
        payload = render(report('r1', full_cells()), out_svg=self.root / 's.svg')
        stats = series(payload, 'openclaw', 'memory_recall')
        self.assertEqual(stats['n_runs'], 1)
        self.assertEqual(stats['n_values'], 1)
        self.assertIsNone(stats['error_band'])
        self.assertIsNone(stats['error'])
        self.assertIsNone(stats['sd'])
        self.assertIn('n=1', stats['note'])
        svg = (self.root / 's.svg').read_text(encoding='utf-8')
        self.assertIn('>n=1<', svg)
        self.assertNotIn('stroke-opacity="0.22"', svg)

    def test_error_band_none_disables_band_even_for_many_runs(self):
        payload = summarize([report('run-%d' % i, full_cells()) for i in (1, 2)], error_band='none')
        stats = series(payload, 'openclaw', 'memory_recall')
        self.assertIsNone(stats['error_band'])
        self.assertEqual(stats['min'], 0.5)
        self.assertEqual(stats['max'], 0.5)

    # ---------------------------------------------------------------- 全部缺失
    def test_all_dimensions_missing_still_renders_without_fabricating_zero(self):
        cells = {a: {dim: ['unassessable', 'unassessable'] for dim in DIMS} for a in AGENTS}
        payload = render(report('r1', cells), out_svg=self.root / 'e.svg',
                         out_json=self.root / 'e.json')
        self.assertTrue(payload['all_missing'])
        for agent in AGENTS:
            entry = next(a for a in payload['agents'] if a['name'] == agent)
            self.assertEqual(len(entry['missing_dimensions']), len(DIMS))
            for dim in entry['dimensions']:
                self.assertIsNone(dim['mean'])
                self.assertIsNone(dim['min'])
                self.assertIsNone(dim['max'])
                self.assertIsNone(dim['sd'])
                self.assertIsNone(dim['error'])
        svg = (self.root / 'e.svg').read_text(encoding='utf-8')
        ET.parse(self.root / 'e.svg')
        self.assertNotIn('polygon agent=', svg)  # 没有任何数据多边形被“闭合”出来
        self.assertNotIn('observed=%s' % DIMS[0], svg)
        self.assertGreaterEqual(svg.count('stroke-dasharray'), 2 * len(DIMS))  # 每 agent 每维一个缺失标记
        for dim in DIMS:
            self.assertIn(radar_v2.ZH[dim], svg)  # 六条轴与标签仍完整出图
        doc = json.loads((self.root / 'e.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['agents'][0]['dimensions'][0]['values'][0]['value'], None)
        self.assertIn('"mean": null', json.dumps(doc, ensure_ascii=False))

    def test_declared_agent_without_any_dimension_is_visible_and_missing(self):
        first = report('r1', {AGENTS[0]: {dim: ['pass'] for dim in DIMS}})
        second = report('r2', {AGENTS[1]: {dim: ['pass'] for dim in DIMS}})
        payload = summarize([first, second])
        self.assertEqual(sorted(a['name'] for a in payload['agents']), sorted(AGENTS))
        stats = series(payload, AGENTS[1], 'memory_recall')
        self.assertEqual(stats['n_values'], 1)
        self.assertEqual(stats['missing_runs'], ['r1'])
        self.assertEqual(stats['missing_reason'], ['agent absent from report'])

    # ---------------------------------------------------------------- JSON 口径与 CLI
    def test_payload_keeps_report_honesty_fields(self):
        payload = render(report('r1', full_cells()), out_svg=self.root / 'p.svg')
        self.assertIsNone(payload['overall_score'])
        self.assertIsNone(payload['ranking'])
        self.assertEqual(payload['source_formats'], ['okb-diagnostic-report-v2-draft'])
        self.assertEqual(payload['source_runs'], ['r1'])
        self.assertEqual(payload['dimensions'][0]['key'], 'long_term_retention')
        self.assertIn('不折算成 0', payload['missing_policy'])
        self.assertIn(MISSING_LABEL, payload['missing_policy'])
        self.assertTrue(any('null' in limit for limit in payload['limits']))
        declared = {a['name']: a for a in payload['agents']}
        self.assertEqual(list(declared), sorted(AGENTS))  # 沿用报告的稳定排序
        self.assertEqual(declared['openclaw']['agent_kind'], 'real')
        self.assertEqual(declared['openclaw']['model'], 'm-openclaw')
        self.assertEqual(declared['openclaw']['temperature'], 0.2)
        self.assertEqual(declared['hermes']['agent_kind'], 'mock')

    def test_common_unit_metric_and_plan_drift_limit(self):
        runs = [report('r1', full_cells()), report('r2', full_cells(), plan_sha='b' * 64)]
        payload = summarize(runs, value_key='common_unit_pass_rate')
        self.assertEqual(series(payload, 'openclaw', 'memory_recall')['mean'], 0.5)
        self.assertTrue(any('plan' in limit for limit in payload['limits']))
        drawn = draw_svg(payload, title='t')
        self.assertIn('<svg ', drawn)

    def test_cli_writes_svg_and_json(self):
        path = self.root / 'r1.json'
        path.write_text(json.dumps(report('r1', full_cells()), ensure_ascii=False),
                        encoding='utf-8', newline='\n')
        out = self.root / 'out' / 'radar.svg'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(['--in', str(path), '--out-svg', str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.is_file())
        self.assertTrue(out.with_suffix('.json').is_file())
        printed = json.loads(stdout.getvalue().strip().splitlines()[-1])
        self.assertIsNone(printed['overall_score'])
        self.assertEqual(printed['agents'], {a: 0 for a in AGENTS})
        no_json = io.StringIO()
        with contextlib.redirect_stdout(no_json):
            self.assertEqual(main(['--in', str(path), '--out-svg', str(self.root / 'n.svg'),
                                   '--no-json', '--error', 'sd']), 0)
        self.assertFalse((self.root / 'n.json').exists())

    def test_cli_rejects_bad_input_without_excess_exit(self):
        path = self.root / 'bad.json'
        path.write_text('{"format":"legacy"}', encoding='utf-8', newline='\n')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(['--in', str(path), '--out-svg', str(self.root / 'x.svg')]), 2)
        self.assertIn('radar_v2 rejected', stderr.getvalue())

    def test_files_are_utf8_with_lf_newlines(self):
        report_path = self.root / 'r.json'
        report_path.write_text(json.dumps(report('r1', full_cells()), ensure_ascii=False),
                               encoding='utf-8', newline='\n')
        svg_path = self.root / 'r.svg'
        with contextlib.redirect_stdout(io.StringIO()):
            main(['--in', str(report_path), '--out-svg', str(svg_path),
                  '--out-json', str(self.root / 'r-out.json')])
        for path in (svg_path, self.root / 'r-out.json'):
            raw = path.read_bytes()
            self.assertNotIn(b'\r\n', raw)
            self.assertIn('缺失'.encode('utf-8'), raw)


if __name__ == '__main__':
    unittest.main()
