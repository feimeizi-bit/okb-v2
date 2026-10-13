#!/usr/bin/env python3
"""Read configured local files and code identities; never runs CLI/health/reset/probe.

Selectors: runtime_identity_sources.{model,temperature} each has path, format
(json/yaml), pointer. Only files declared by the selected adapter are permitted.
Output is private declared configuration, not proof of effective settings.
"""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from engine.runtime_identity import capture
from scoring.fact_judge import strict_json


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--agent',required=True,choices=['openclaw','hermes'])
    p.add_argument('--config',required=True,help='local adapter configuration JSON; not read from environment')
    p.add_argument('--out',required=True)
    args=p.parse_args(argv)
    try:
        out=Path(args.out).resolve();source=Path(args.config).resolve()
        if out.exists() or not out.parent.is_dir():raise ValueError('new output required')
        data=source.read_bytes();config=strict_json(data.decode('utf8'))
        if not isinstance(config,dict):raise ValueError('config object required')
        if args.agent=='openclaw':
            from engine.adapters.openclaw import OpenClawAdapter as Adapter
        else:
            from engine.adapters.hermes import HermesAdapter as Adapter
        adapter=Adapter(config)
        result=capture(adapter)
        if source.read_bytes()!=data:raise ValueError('input changed')
        from engine.runtime_identity import unchanged
        if not unchanged(adapter,result):raise ValueError('configuration changed')
        with out.open('x',encoding='utf8',newline='\n') as f:
            json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
        print(json.dumps(dict(output=str(out),unresolved=result['runtime_identity']['errors'],effective_settings_verified=False)))
        return 1 if result['runtime_identity']['errors'] else 0
    except (OSError,ValueError,KeyError,TypeError):
        print('Runtime identity capture rejected',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
