#!/usr/bin/env python3
"""Inspect a call journal and optionally export valid responses, entirely offline."""
import argparse
import json
from pathlib import Path
from scoring.audited_facts import read_journal, recovered_records


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--journal', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--out', help='New replay JSONL; never overwrites or resumes HTTP')
    args=ap.parse_args(argv)
    try:
        journal=read_journal(args.journal)
        rows=recovered_records(args.journal,model=args.model)
        if args.out:
            with Path(args.out).open('x',encoding='utf8',newline='\n') as f:
                for row in rows:
                    f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(json.dumps(dict(attempts=len(journal['pending']), recovered=len(rows),
              unknown_requests=journal['unknown_requests'], incomplete_tail=journal['incomplete_tail'],
              live_resume_supported=False),ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print('Journal recovery rejected: %s: %s' % (type(exc).__name__, exc))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
