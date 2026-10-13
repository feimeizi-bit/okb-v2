"""Prepare a declared judge cost group offline; never authorize or send calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from scoring.cost_group import CostGroup, cost_group, initialize_ledger
from scoring.fact_judge import strict_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', action='append', required=True, help='Unique scope-id=runtime-contract-path; repeatable')
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--currency', required=True)
    parser.add_argument('--max-total-cost', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args(argv)
    try:
        out = Path(args.out_dir).resolve()
        if out.exists() or not out.parent.is_dir():
            raise ValueError('group output must be new with existing parent')
        snapshots = {}
        scopes = []
        for value in args.scope:
            identifier, separator, name = value.partition('=')
            if not separator or not name:
                raise ValueError('scope must be scope-id=contract-path')
            path = Path(name).resolve()
            if path.is_relative_to(out):
                raise ValueError('group output overlaps input')
            raw = path.read_bytes()
            if path in snapshots and snapshots[path] != raw:
                raise ValueError('contract drift')
            snapshots[path] = raw
            scopes.append((identifier, strict_json(raw.decode('utf8'))))
        policy = cost_group(scopes, group_id=args.group_id, currency=args.currency,
            max_total_cost=args.max_total_cost, ledger_path=out/'cost-ledger.sqlite3')
        if any(path.read_bytes() != raw for path,raw in snapshots.items()):
            raise ValueError('contracts changed during group preparation')
        out.mkdir()
        initialize_ledger(policy)
        report = dict(summary=CostGroup(policy).summary(), real_http_calls=0,
            execution_authorized=False, mutable_ledger_excluded_from_complete=True,
            source_fingerprints={str(path):hashlib.sha256(raw).hexdigest() for path,raw in snapshots.items()},
            limitations=['Declaration does not prove human authorization or provider hard caps.',
                'One currency, explicit judge scopes only; Agent/tool/control budgets remain separate.',
                'Pending/unknown/halted consumption blocks all scopes. No live recovery or retries.',
                'Ledger is serial and path-bound; no automated concurrency or fresh-ledger fallback.',
                'Hash chains are not protection against an owner replacing or rewriting the entire ledger.'])
        for name, value in [('authorization-group.json', policy), ('review.json', report)]:
            with (out/name).open('x', encoding='utf8', newline='\n') as stream:
                stream.write(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
        manifest = {name:hashlib.sha256((out/name).read_bytes()).hexdigest()
            for name in ('authorization-group.json', 'review.json')}
        with (out/'complete.json').open('x', encoding='utf8', newline='\n') as stream:
            stream.write(json.dumps(manifest, indent=2)+'\n')
        print(json.dumps(dict(group_id=policy['group_id'], scopes=len(scopes), execution_authorized=False)))
        return 0
    except Exception as error:
        print('cost group rejected: '+type(error).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
