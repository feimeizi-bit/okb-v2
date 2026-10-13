#!/usr/bin/env python3
"""Prepare/review a private semantic family manifest offline; no model or Agent calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from scoring.fact_judge import strict_json
from scoring.semantic_split import template, review, sha


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('prepare', 'review'))
    ap.add_argument('--source', action='append', required=True, help='Explicit ID=local-path; repeat for every source')
    ap.add_argument('--manifest')
    ap.add_argument('--out-dir', required=True)
    a = ap.parse_args(argv)
    try:
        if (a.action == 'review') != bool(a.manifest):
            raise ValueError('review requires manifest; prepare forbids it')
        paths, sources = {}, {}
        for argument in a.source:
            sid, path = argument.split('=', 1)
            resolved = Path(path).resolve()
            if sid in sources or resolved in paths.values():
                raise ValueError('duplicate source')
            paths[sid] = resolved
            sources[sid] = resolved.read_bytes()
        inputs = {paths[sid]:raw for sid, raw in sources.items()}
        if a.manifest:
            path = Path(a.manifest).resolve()
            if path in inputs:
                raise ValueError('manifest overlaps corpus')
            manifest_raw = path.read_bytes(); inputs[path] = manifest_raw
            manifest = strict_json(manifest_raw.decode('utf8'))
        else:
            manifest = template(sources)
            manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode('utf8')
        report = review(manifest, sources)
        out = Path(a.out_dir).resolve()
        if out.exists() or not out.parent.is_dir() or any(p.is_relative_to(out) for p in inputs):
            raise ValueError('output must be new and outside inputs')
        report['source_fingerprints'] = {str(p):sha(raw) for p, raw in inputs.items()}
        report['manifest_sha256'] = sha(manifest_raw)
        def unchanged():
            if any(p.read_bytes() != raw for p, raw in inputs.items()):
                raise ValueError('input changed during review')
        unchanged(); out.mkdir()
        with (out/'manifest.json').open('xb') as f:
            f.write(manifest_raw)
        with (out/'review.json').open('x', encoding='utf8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2); f.write('\n')
        unchanged()
        with (out/'complete.json').open('x', encoding='utf8', newline='\n') as f:
            json.dump({name:sha((out/name).read_bytes()) for name in ('manifest.json', 'review.json')}, f, indent=2)
        print(json.dumps({k:report[k] for k in ('total_cases', 'validation_candidate_cases', 'unmet_checks', 'freeze_accepted')}))
        return 0 if report['offline_checks_passed'] else 1
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print('Semantic split review rejected: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
