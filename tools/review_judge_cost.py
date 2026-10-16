#!/usr/bin/env python3
"""Prepare an offline request inventory/cost scenario. No network or execution path.

Exit 0 means an artifact was generated, not that a budget was approved or priced.
Only author development cases and optionally a traced v2/scoped prepare are read.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT)]
from scoring.fact_judge import strict_json
from scoring.report_v2 import load_bundle, sha
from scoring.cost_review import WIRE_PROFILES, inventory, scenario, wire_body
from scoring.cost_runtime import runtime_contract
from semantic_robustness import build_requests


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', default=str(ROOT/'samples/semantic-robustness.v1.json'))
    p.add_argument('--history-only', action='store_true', help='Review only traced history, without development cases')
    for name in ('score-dir', 'run-dir', 'paper', 'plan', 'model', 'base-url', 'quote', 'token-counts'):
        p.add_argument('--'+name)
    p.add_argument('--max-output-tokens', type=int, required=True)
    p.add_argument('--wire-profile', choices=WIRE_PROFILES, default='legacy-chat-v1')
    p.add_argument('--reasoning-effort', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    p.add_argument('--runtime-budget', help='Optional runtime budget; requires quote and token counts')
    p.add_argument('--max-calls', type=int, help='Exact reviewed call count; requires runtime-budget')
    p.add_argument('--out-dir', required=True)
    a = p.parse_args(argv)
    try:
        out = Path(a.out_dir).resolve(); snapshots = {}; run_files = None
        paths = [a.score_dir, a.run_dir, a.paper, a.plan]
        if any(paths) and not all(paths):
            raise ValueError('history needs score-dir/run-dir/paper/plan together')
        if a.history_only and not all(paths):
            raise ValueError('history-only requires a traced history bundle')
        if out.exists() or not out.parent.is_dir() or any(out.is_relative_to(Path(d).resolve()) for d in (a.score_dir, a.run_dir) if d):
            raise ValueError('output must be new and outside source bundles')
        def read(path):
            path = Path(path).resolve(); data = path.read_bytes()
            if path in snapshots and snapshots[path] != data:
                raise ValueError('input drift')
            snapshots[path] = data
            return strict_json(data.decode('utf8'))
        requests = []
        if not a.history_only:
            corpus = read(a.corpus)
            requests = [dict(r, source=dict(kind='author_development_case', case_id=r['case_id']))
                        for r in build_requests(corpus)]
        history = []
        if all(paths):
            bundle, loaded, run_files = load_bundle(*(Path(d).resolve() for d in paths), verify_trace=True)
            if bundle['summary']['mode'] != 'prepare' or bundle['summary'].get('evidence_policy') != 'scoped-v1':
                raise ValueError('history must be v2/scoped prepare; no resend estimates')
            for path, data in loaded.items():
                if path in snapshots and snapshots[path] != data:
                    raise ValueError('input drift')
                snapshots[path] = data
            rows = strict_json(loaded[(Path(a.score_dir)/'results.json').resolve()].decode('utf8'))
            for row in rows:
                generated = {r['check_id'] for r in row['semantic_requests']}
                history.append(dict(task_id=row['task_id'], layer1=row['layer1'],
                    pending_requests=len(generated),
                    blocked_semantic_checks=[c for c in row['layer3'] if c['id'] not in generated]))
                requests.extend(dict(r, source=dict(kind='historical_prepare', task_id=row['task_id'], check_id=r['check_id']))
                                for r in row['semantic_requests'])
        inv = inventory(requests, model=a.model, base_url=a.base_url, max_output_tokens=a.max_output_tokens,
                        wire_profile=a.wire_profile, reasoning_effort=a.reasoning_effort)
        quote = read(a.quote) if a.quote else None
        tokens = read(a.token_counts) if a.token_counts else None
        estimate = scenario(inv, quote, tokens)
        if bool(a.runtime_budget) != (a.max_calls is not None):
            raise ValueError('runtime budget and exact call cap must be supplied together')
        contract = runtime_contract(inv, quote, tokens, read(a.runtime_budget), a.max_calls) if a.runtime_budget else None
        report = dict(**inv, estimate=estimate, historical_tasks=history,
            history_source_invalid=sum(t['layer1']['valid'] is False for t in history),
            real_http_calls=0, execution_authorized=False, semantic_reliability_validated=False,
            runtime_binding=dict(status='bound_declared_scenario' if contract else 'not_bound',
                contract_sha256=contract['contract_sha256'] if contract else None),
            limitations=['Byte lengths are serialization sizes, not token counts.',
                'No provider prices, credentials or tokenizer are fetched.',
                'Development corpus is not independent semantic validation.',
                'Invalid historical evidence stays excluded from calls and visible in this review.'],
            source_fingerprints={str(path): sha(data) for path, data in snapshots.items()})
        def unchanged():
            if any(path.read_bytes() != data for path, data in snapshots.items()):
                raise ValueError('inputs changed during review')
            if run_files is not None and {f.resolve() for f in Path(a.run_dir).rglob('*') if f.is_file()} != run_files:
                raise ValueError('run file set changed')
        unchanged(); out.mkdir()
        def write(name, value):
            with (out/name).open('x', encoding='utf8', newline='\n') as f:
                f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        write('review.json', report)
        write('requests.json', requests)
        names = ['review.json', 'requests.json']
        if a.wire_profile == 'openai-cache-chat-v1':
            wire_rows = [dict(request_sha256=row['request_sha256'],
                wire_request_sha256=inv['requests'][position]['wire_request_sha256'],
                body=wire_body(row['request'],a.model,a.max_output_tokens,
                    wire_profile=a.wire_profile,reasoning_effort=a.reasoning_effort))
                for position,row in enumerate(requests)]
            write('wire-requests.json', dict(format='okb-judge-wire-requests-v1-draft',
                model=a.model,endpoint_sha256=inv['endpoint_sha256'],records=wire_rows))
            names.append('wire-requests.json')
        if contract:
            write('runtime-contract.json', contract)
            names.append('runtime-contract.json')
        unchanged()
        write('complete.json', {n: sha((out/n).read_bytes()) for n in names})
        print(json.dumps(dict(directory=str(out), requests=len(requests),
            history_source_invalid=report['history_source_invalid'], estimate=estimate['status'],
            total_cost=estimate['total_cost'], execution_authorized=False), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('Cost review rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
