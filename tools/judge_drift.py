#!/usr/bin/env python3
"""Explicit drift batches: --collect makes new calls; --replay cannot call out.

Bare/legacy implicit collection is refused. --same-day aliases --replay, but an
explicit batch and a new report path are required. Historical cache and markdown
log are untouched. See docs/judge-drift-batches.md for the acquisition protocol.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / 'tools', ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scoring import anchors as A
from scoring.index import EvidenceIndex
from scoring.judge_client import OpenAICompatJudge, JudgeError, PROMPT_TEMPLATE_VERSION, ALLOWED
from loader import load_jsonl

FORMAT = 'okb-drift-batch-v1'
TIERS = ('remember', 'coexist', 'replace', 'forget', 'gamed')
BATCH_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')


class DriftError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_sha(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    """Exclusive write: cannot overwrite source results, inputs or past reports."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
        fh.write('\n')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def batch_path(root, name):
    if not name or not BATCH_ID.fullmatch(name):
        raise DriftError('invalid batch ID (ASCII letters/digits/_/- only)')
    root = root.resolve()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise DriftError('batch path must not redirect outside batch root')
    return path


def prepare(cases_path, paper_path, runs_root):
    """All prompts/evidence must be available before any paid call or new batch."""
    doc = load_json(cases_path)
    cases = doc.get('cases')
    if not isinstance(cases, list) or not cases:
        raise DriftError('nonempty cases required')
    samples = load_jsonl(str(paper_path))
    paper = {s['id']: s for s in samples}
    if len(paper) != len(samples):
        raise DriftError('duplicate paper IDs')
    prepared, seen, tasks, indexes = [], set(), set(), {}
    for c in cases:
        cid, tier, sid, mode = (c.get(k) for k in ('case_id', 'tier', 'sample_id', 'mode'))
        if not isinstance(cid, str) or not cid or cid in seen:
            raise DriftError('missing/duplicate case ID')
        if tier not in TIERS or mode not in ('confirm', 'shadow') or sid not in paper:
            raise DriftError('unknown tier/mode/sample')
        identity = (tier, sid, mode)
        if identity in tasks:
            raise DriftError('duplicate case task/mode (would reuse a collection cache key)')
        seen.add(cid)
        tasks.add(identity)
        expected = c.get('expected')
        if not c.get('unstable') and (not isinstance(expected, list) or not expected
                or any(v not in ALLOWED for v in expected)):
            raise DriftError('invalid expected band')
        if tier not in indexes:
            indexes[tier] = EvidenceIndex.load(str(runs_root / ('mock-c2-v02-' + tier)))
        idx, sample = indexes[tier], paper[sid]
        task = 'mock-' + sid
        if not idx.task_rows(task):
            raise DriftError('case task has no evidence')
        outs = []
        for a in sample.get('anchors', []):
            # Fail before acquisition instead of silently discarding failed anchors.
            outcome = A.evaluate(sample, a, idx, task)
            if mode == 'shadow' or outcome.severity == 'soft' or outcome.result == 'skip':
                outs.append(outcome)
        if not outs:
            raise DriftError('case has no applicable anchor evidence')
        prompt = OpenAICompatJudge.build_prompt(None, sample, outs, idx, task, shadow=mode == 'shadow')
        prepared.append({'case': c, 'sample': sample, 'idx': idx, 'task': task,
                         'outcomes': outs, 'prompt_sha256': sha(prompt.encode('utf-8'))})
    fingerprints = {
        'cases_sha256': sha(Path(cases_path).read_bytes()),
        'paper_sha256': sha(Path(paper_path).read_bytes()),
        'prompts': {p['case']['case_id']: p['prompt_sha256'] for p in prepared},
        'template': PROMPT_TEMPLATE_VERSION,
        'judge_version': OpenAICompatJudge.version,
    }
    return prepared, fingerprints


def read_batch(path):
    meta, result = load_json(path / 'metadata.json'), load_json(path / 'result.json')
    if meta.get('format') != FORMAT or result.get('format') != FORMAT:
        raise DriftError('unsupported batch format')
    if result.get('metadata_sha256') != json_sha(meta):
        raise DriftError('batch metadata changed')
    if not result.get('complete') or result.get('mode') != 'collect':
        raise DriftError('incomplete/non-collection batch is not replayable or a baseline')
    cache = path / 'judge_cache.jsonl'
    if not cache.is_file() or sha(cache.read_bytes()) != result.get('cache_sha256'):
        raise DriftError('batch cache missing or changed')
    if result.get('model') != meta.get('model') or result.get('template') != meta['inputs']['template']:
        raise DriftError('batch result identity mismatch')
    records = result.get('records', [])
    if not isinstance(records, list) or len(records) != len(meta['inputs']['prompts']):
        raise DriftError('batch result coverage mismatch')
    if {r.get('case_id') for r in records} != set(meta['inputs']['prompts']):
        raise DriftError('batch result IDs mismatch')
    if any(r.get('verdict') not in ALLOWED or r.get('status') != 'ok' for r in records):
        raise DriftError('batch contains unavailable/invalid decisions')
    if any(r.get('prompt_sha256') != meta['inputs']['prompts'][r['case_id']] for r in records):
        raise DriftError('batch result prompt mismatch')
    if (result.get('successful_calls') != len(records) or result.get('cache_hits') != 0
            or result.get('attempted_requests') != len(records)):
        raise DriftError('source batch did not acquire every case afresh')
    cached_rows = [json.loads(line) for line in cache.read_text(encoding='utf-8').splitlines() if line.strip()]
    cached = {r['cache_key']: r for r in cached_rows}
    if len(cached) != len(cached_rows) or set(cached) != {r.get('cache_key') for r in records}:
        raise DriftError('source cache keys do not match collection records')
    for row in records:
        hit = cached[row['cache_key']]
        version = meta['inputs']['judge_version'] + (':shadow' if row['mode'] == 'shadow' else '')
        if (hit.get('verdict') != row['verdict'] or hit.get('model') != meta['model']
                or hit.get('template') != meta['inputs']['template'] or hit.get('judge_version') != version):
            raise DriftError('source cache decision/identity differs from result')
    return meta, result


def assert_compatible(meta, inputs, model):
    if meta.get('model') != model or meta.get('inputs') != inputs:
        raise DriftError('model, template or case/paper/prompt fingerprint differs; open a new baseline')


def replay_client(model, cache_dir):
    # Deliberately bypass __init__: no .env, endpoint, API key or credentials.
    judge = OpenAICompatJudge.__new__(OpenAICompatJudge)
    judge.model, judge.cache_dir = model, str(cache_dir)
    judge.allowed = ALLOWED
    judge.max_calls, judge.calls, judge.cache_hits, judge.total_tokens = 0, 0, 0, 0
    judge._cache = {r['cache_key']: r for r in
                    (json.loads(line) for line in (cache_dir / 'judge_cache.jsonl').read_text(encoding='utf-8').splitlines() if line.strip())}

    def no_network(*_args, **_kwargs):
        raise DriftError('network forbidden during replay')
    judge._call = no_network
    judge._store_cache = no_network
    return judge


def evaluate_cases(judge, prepared):
    records = []
    for p in prepared:
        c = p['case']
        record = {'case_id': c['case_id'], 'mode': c['mode'],
                  'prompt_sha256': p['prompt_sha256'],
                  'eligible_for_stability': not (c.get('unstable') or c.get('band_provisional') or c.get('adjudication')),
                  'provisional': bool(c.get('band_provisional')),
                  'unstable': bool(c.get('unstable')), 'watch': bool(c.get('adjudication'))}
        try:
            d = judge.judge_soft(p['sample'], p['outcomes'], p['idx'], p['task'], shadow=c['mode'] == 'shadow')
            if d.verdict not in ALLOWED:
                raise JudgeError('invalid verdict')
            record.update(status='ok', verdict=d.verdict, cache_key=d.cache_key,
                          in_expected_band=None if c.get('unstable') else d.verdict in c['expected'])
        except JudgeError:
            # Never persist raw exceptions (may include endpoint/body/credentials).
            record.update(status='judge_error', verdict=None, cache_key=None, in_expected_band=None)
        records.append(record)
    return records


def compare_batches(previous, records, started_at, baseline_at):
    old = {r['case_id']: r for r in previous['records']}
    changed, eligible_changed = [], []
    for row in records:
        prior = old[row['case_id']]
        if row['status'] == 'ok' and row['verdict'] != prior['verdict']:
            changed.append(row['case_id'])
            if row['eligible_for_stability']:
                eligible_changed.append(row['case_id'])
    return {
        'kind': 'cross_day_new_collection' if started_at[:10] != baseline_at[:10] else 'same_day_new_collection',
        'changed': len(changed), 'changed_cases': changed,
        'eligible_total': sum(r['eligible_for_stability'] for r in records),
        'eligible_changed': len(eligible_changed), 'eligible_changed_cases': eligible_changed,
        'interpretation': 'descriptive transitions only; not statistical significance or a stability claim',
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--collect', action='store_true', help='explicit paid acquisition; use a never-used batch ID')
    mode.add_argument('--replay', '--same-day', dest='replay', action='store_true', help='offline cache replay, never calls the network')
    ap.add_argument('--batch-id', required=True)
    ap.add_argument('--batch-root', type=Path, default=ROOT / 'build/judge-drift')
    ap.add_argument('--model', help='mandatory for collect; replay defaults to recorded model')
    ap.add_argument('--max-calls', type=int, help='mandatory logical request budget for collect; each request may retry up to 3 HTTP times')
    ap.add_argument('--baseline', help='previous completed collection batch ID, same inputs/model required')
    ap.add_argument('--report', type=Path, help='new output JSON path required for replay; never overwrites files')
    ap.add_argument('--cases', type=Path, default=ROOT / 'samples/judge-anchor-cases.v0.1.json')
    ap.add_argument('--paper', type=Path, default=ROOT / 'build/paper-v0.2.jsonl')
    ap.add_argument('--runs-root', type=Path, default=ROOT / 'runs')
    a = ap.parse_args(argv)
    try:
        target = batch_path(a.batch_root, a.batch_id)
        if a.collect and (not a.model or not a.model.strip() or a.max_calls is None or a.max_calls <= 0 or a.report):
            raise DriftError('collect requires explicit model and positive max-calls; report is batch/result.json')
        if a.replay and (not a.report or a.baseline or a.max_calls is not None):
            raise DriftError('replay requires a new report path; baseline/max-calls are collection-only')
        if a.collect and target.exists():
            raise DriftError('batch already exists; never reuse or overwrite an acquisition batch')
        if a.replay and (a.report.exists() or a.report.resolve().is_relative_to(a.batch_root.resolve())):
            raise DriftError('replay report must be a new file outside the batch root')
        prepared, inputs = prepare(a.cases, a.paper, a.runs_root)
        baseline = None
        if a.collect:
            if a.max_calls < len(prepared):
                raise DriftError('max-calls must cover every case before starting acquisition')
            if a.baseline:
                bm, br = read_batch(batch_path(a.batch_root, a.baseline))
                assert_compatible(bm, inputs, a.model)
                baseline = (bm, br)
            # Atomic directory reservation prevents two collectors sharing cache.
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir(exist_ok=False)
            meta = {'format': FORMAT, 'batch_id': a.batch_id, 'started_at': now(),
                    'model': a.model, 'inputs': inputs, 'max_logical_requests': a.max_calls,
                    'http_attempt_upper_bound': 3 * a.max_calls,
                    'baseline_id': a.baseline,
                    'baseline_result_sha256': sha((batch_path(a.batch_root, a.baseline) / 'result.json').read_bytes()) if baseline else None}
            write_new(target / 'metadata.json', meta)
            try:
                judge = OpenAICompatJudge(str(ROOT), model=a.model, cache_dir=str(target))
            except (JudgeError, ValueError):
                write_new(target / 'result.json', {'format': FORMAT, 'mode': 'collect', 'complete': False,
                          'metadata_sha256': json_sha(meta), 'error': 'judge initialization failed; no acquisition',
                          'attempted_requests': 0, 'successful_calls': 0, 'cache_hits': 0,
                          'unavailable': len(prepared), 'records': [], 'finished_at': now()})
                return 1
            judge.max_calls = a.max_calls
            attempts = [0]
            original_call = judge._call

            def counted_call(prompt):
                if attempts[0] >= a.max_calls:
                    raise JudgeError('logical request budget exhausted')
                attempts[0] += 1  # counts failed requests too; judge.calls counts successes only
                return original_call(prompt)
            judge._call = counted_call
            source = None
        else:
            meta, source = read_batch(target)
            assert_compatible(meta, inputs, a.model or meta['model'])
            judge = replay_client(meta['model'], target)
            attempts = [0]
        records = evaluate_cases(judge, prepared)
        unavailable = sum(r['status'] != 'ok' for r in records)
        hard = [r['case_id'] for r in records if r['in_expected_band'] is False and not r['watch']]
        pending = [r['case_id'] for r in records if r['in_expected_band'] is False and r['watch']]
        counter_ok = ((judge.calls == len(prepared) and judge.cache_hits == 0) if a.collect else
                      (judge.calls == 0 and judge.cache_hits == len(prepared)))
        replay_diffs = []
        if source:
            old = {r['case_id']: r for r in source['records']}
            replay_diffs = [r['case_id'] for r in records if r != old[r['case_id']]]
        complete = not unavailable and counter_ok and not replay_diffs
        result = {'format': FORMAT, 'mode': 'collect' if a.collect else 'replay',
                  'metadata_sha256': json_sha(meta), 'finished_at': now(),
                  'model': meta['model'], 'template': inputs['template'], 'judge_version': inputs['judge_version'],
                  'attempted_requests': attempts[0], 'successful_calls': judge.calls,
                  'cache_hits': judge.cache_hits, 'http_attempt_upper_bound': 3 * attempts[0],
                  'http_attempts_exact': None, 'total_tokens_reported': judge.total_tokens,
                  'complete': complete, 'unavailable': unavailable, 'counter_ok': counter_ok,
                  'hard_violations': hard, 'pending': pending, 'replay_diffs': replay_diffs,
                  'eligible_for_stability': sum(r['eligible_for_stability'] for r in records),
                  'provisional_cases': [r['case_id'] for r in records if r['provisional']],
                  'unstable_cases': [r['case_id'] for r in records if r['unstable']],
                  'watch_cases': [r['case_id'] for r in records if r['watch']],
                  'records': records, 'comparison': None}
        cache = target / 'judge_cache.jsonl'
        result['cache_sha256'] = sha(cache.read_bytes()) if cache.exists() else None
        if a.replay:
            result['source_result_sha256'] = sha((target / 'result.json').read_bytes())
        if baseline and complete:
            result['comparison'] = compare_batches(baseline[1], records, meta['started_at'], baseline[0]['started_at'])
        result['exit_code'] = 0 if complete and not hard else 1
        write_new(target / 'result.json' if a.collect else a.report, result)
        print(json.dumps({k: result[k] for k in ('mode', 'complete', 'attempted_requests', 'successful_calls',
                          'cache_hits', 'unavailable', 'hard_violations', 'pending', 'exit_code')}, ensure_ascii=False))
        return result['exit_code']
    except (DriftError, JudgeError, OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        # Show actionable local validation errors, not transport exception bodies.
        message = str(exc) if isinstance(exc, DriftError) else type(exc).__name__
        print('FAIL drift: ' + message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
