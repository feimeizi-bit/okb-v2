"""Generate authored controls and replay the actual mock-engine/v2/report pipeline offline."""
import argparse
import contextlib
import json
from pathlib import Path
import sys

from portable.bundle import encode, sha, verify


def write(path, value):
    with path.open('xb') as stream:
        stream.write(encode(value))


def fixtures():
    paper = []
    policies = []
    plans = []
    for suffix, value, layer in [('pass', 1, 'objective'), ('fail', 2, 'objective'),
                                 ('semantic', 1, 'semantic'), ('memory', 1, 'memory')]:
        sample_id = 'portable_' + suffix
        memory = layer == 'memory'
        sample = dict(id=sample_id, dimension='long_term_retention' if memory else 'task_reuse',
                      meta=dict(suite_version='portable-fixture-v1'), score_policy='rule_only',
                      expect=dict(verdict='remembered'),
                      script=[dict(session='s1', phase='write', turns=[dict(text='Remember the authored fixture preference: red.')]),
                              dict(session='s2', phase='probe', turns=[dict(text='Write result.json containing the fixture value.')])],
                      anchors=[dict(id='A1', type='memory_entry' if memory else 'file_content',
                                    expect='present', severity='hard', on_violation='omitted',
                                    **({'key': '*', 'value_regex': 'red'} if memory else {'path': 'result.json'}))])
        paper.append(sample)
        policies.append(dict(sample_id=sample_id, turns=[
            dict(match=dict(session='s1', turn=1), memory_ops=[dict(op='set', key='fixture', value='The fixture preference is red.')]),
            dict(match=dict(session='s2', turn=1), file_ops=[dict(op='write', path='result.json', content=json.dumps({'value': value, 'preference': 'red'}))])]))
        check = dict(id='check', layer='semantic' if layer != 'objective' else 'objective',
                     on_failure='omitted', basis='s1.t1' if memory else 's2.t1')
        if layer == 'objective':
            check.update(op='json_field_equals', path='result.json', field='/value', value=1)
        else:
            check.update(scope='memory_after' if memory else 'file_final',
                         requirement='The current authored fixture preference is red.')
            if not memory:
                check['path'] = 'result.json'
        plans.append(dict(sample_id=sample_id, checks=[check], anchor_disposition={'A1': dict(
            role=check['layer'], checks=['check'], reason='authored engineering control, not independent validation')}))
    return paper, dict(policy_version=1, tier='remember', on_missing_record='fail',
                       defaults=dict(reply_text='Authored offline fixture.'), samples=policies), plans


def text_quote(content):
    leaves = []
    def walk(value, pointer):
        if isinstance(value, str) and value:
            leaves.append((pointer, value))
        elif isinstance(value, dict):
            for key, child in value.items():
                walk(child, pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, pointer + '/' + str(index))
    walk(content, '')
    if not leaves:
        raise ValueError('fixture observation has no quotable text')
    return max(leaves, key=lambda item: len(item[1]))


def execute(root, out):
    from simulate import simulate
    from score_four_layer import main as score
    from report_four_layer import main as report
    from scoring.fact_judge import VERSION, fingerprint

    root, out = Path(root).resolve(), Path(out).absolute()
    if out.exists() or not out.parent.is_dir() or out.resolve().is_relative_to(root):
        raise ValueError('selfcheck output must be new and outside bundle')
    before = verify(root)
    out.mkdir()
    samples, policy, rows = fixtures()
    paper = out / 'paper.jsonl'
    with paper.open('xb') as stream:
        for sample in samples:
            stream.write((json.dumps(sample, ensure_ascii=False) + '\n').encode('utf8'))
    policy_path = out / 'policy.json'
    write(policy_path, policy)
    plan_path = out / 'plan.json'
    write(plan_path, dict(format='okb-four-layer-v1', paper_sha256=sha(paper.read_bytes()), samples=rows))
    with (out / 'pipeline.log').open('w', encoding='utf8') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        run_dir, tasks = simulate(str(paper), str(policy_path), 'portable-authored-fixture', str(out / 'runs'))
        arguments = ['--paper', str(paper), '--plan', str(plan_path), '--run-dir', str(run_dir),
                     '--outcome-version', 'v2', '--evidence-policy', 'scoped-v1']
        prepare = out / 'prepare'
        if score(arguments + ['--out-dir', str(prepare)]) != 1 or not (prepare / 'complete.json').is_file():
            raise ValueError('fixture prepare did not complete with expected unassessable controls')
        requests = json.loads((prepare / 'semantic-requests.json').read_text(encoding='utf8'))
        if len(requests) != 1:
            raise ValueError('scoped memory guard or semantic fixture routing changed')
        request = requests[0]['request']
        observation = request['observations'][0]
        pointer, quote = text_quote(observation['content'])
        replay = out / 'authored-replay.jsonl'
        record = dict(protocol=VERSION, model='portable-authored-control', request_sha256=fingerprint(request),
                      response=dict(result='supported', rationale='Programmed engineering fixture, not a human or model judgment.',
                                    citations=[dict(evidence_id=observation['evidence_id'], pointer=pointer, quote=quote)]))
        with replay.open('xb') as stream:
            stream.write((json.dumps(record, ensure_ascii=False) + '\n').encode('utf8'))
        for name in ('replay-1', 'replay-2'):
            if score(arguments + ['--out-dir', str(out / name), '--replay', str(replay), '--model', 'portable-authored-control']) != 1:
                raise ValueError('expected unresolved memory control was not preserved')
            if not (out / name / 'complete.json').is_file():
                raise ValueError('incomplete fixture replay')
        first = (out / 'replay-1/results.json').read_bytes()
        if first != (out / 'replay-2/results.json').read_bytes():
            raise ValueError('same-run replay is not deterministic')
        outcomes = {row['sample_id']: row['task_outcome'] for row in json.loads(first)}
        expected = {'portable_pass': 'pass', 'portable_fail': 'fail', 'portable_semantic': 'pass', 'portable_memory': 'unassessable'}
        if outcomes != expected or len(tasks) != 4:
            raise ValueError('fixture outcomes differ from authored routing expectations')
        if report(['--score-dir', str(out / 'replay-1'), '--run-dir', str(run_dir), '--paper', str(paper),
                   '--plan', str(plan_path), '--out-dir', str(out / 'report'), '--verify-traces']) != 0:
            raise ValueError('offline trace report failed')
    if verify(root) != before:
        raise ValueError('bundle changed during selfcheck')
    summary = dict(format='okb-portable-selfcheck-v1-draft', authored_engineering_controls=4,
                   task_outcomes=outcomes, complete=True, same_run_replay_byte_identical=True,
                   report_trace_verified=True, bundle_unchanged=True,
                   real_agent_calls=0, model_http_calls=0, human_labels=0, independent_validation_cases=0,
                   semantic_reliability_validated=False, fresh_openkylin_accepted=False, release_authorized=False,
                   limits=['Mock engine plus programmed replay only, not semantic accuracy or real Agent acceptance.',
                           'Same-run replay is not a fresh real run or cross-platform acceptance.'])
    write(out / 'summary.json', summary)
    members = {path.relative_to(out).as_posix(): sha(path.read_bytes()) for path in sorted(out.rglob('*')) if path.is_file()}
    write(out / 'complete.json', members)
    return summary


def main(argv=None, *, root):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', required=True)
    arguments = parser.parse_args(argv)
    try:
        summary = execute(root, arguments.out_dir)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print('Portable selfcheck rejected: ' + type(error).__name__, file=sys.stderr)
        return 2
