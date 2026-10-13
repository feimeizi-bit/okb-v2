#!/usr/bin/env python3
"""Offline control trace verification, never performs a probe, cleanup or model call."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from scoring.fact_judge import strict_json
from scoring.memory_provenance import verify_control_trace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', required=True)
    parser.add_argument('--expected-binding', required=True,
                        help='Separately frozen deployment identity/scope, not copied from the trace')
    parser.add_argument('--out', required=True, help='New result file outside input files')
    args = parser.parse_args(argv)
    try:
        trace_path, binding_path, out = [Path(p).resolve() for p in (args.trace, args.expected_binding, args.out)]
        if out.exists() or out in (trace_path, binding_path) or not out.parent.is_dir() or trace_path == binding_path:
            raise ValueError('inputs must be separate; output must be a new file')
        inputs = {p: p.read_bytes() for p in (trace_path, binding_path)}
        result = verify_control_trace(strict_json(inputs[trace_path].decode('utf8')),
                                     expected_binding=strict_json(inputs[binding_path].decode('utf8')))
        result['trace_sha256'] = hashlib.sha256(inputs[trace_path]).hexdigest()
        result['expected_binding_sha256'] = hashlib.sha256(inputs[binding_path]).hexdigest()
        if any(p.read_bytes() != data for p, data in inputs.items()):
            raise ValueError('control input changed during verification')
        with out.open('x', encoding='utf8', newline='\n') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('Control verification rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
