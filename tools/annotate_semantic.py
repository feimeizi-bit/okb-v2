#!/usr/bin/env python3
"""Offline development semantic human packets, labels and disagreement records.

Never performs human annotation, paid calls, automatic gold creation or release.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT)]
from scoring.fact_judge import strict_json
from scoring.semantic_annotation import make_packets, template, validate_labels, compare, sha


def write(path, obj):
    with path.open('x', encoding='utf8', newline='\n') as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def new_dirs(paths):
    for a in paths:
        if a.exists() or not a.parent.is_dir() or any(a != b and (a.is_relative_to(b) or b.is_relative_to(a)) for b in paths):
            raise ValueError('separate new non-nested directories with existing parents required')
    if len(set(paths)) != len(paths):
        raise ValueError('duplicate output directories')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    make = sub.add_parser('prepare')
    make.add_argument('--corpus', default=str(ROOT/'samples/semantic-robustness.v1.json'))
    for name in ('rater-a-dir', 'rater-b-dir', 'private-dir'):
        make.add_argument('--'+name, required=True)
    val = sub.add_parser('validate')
    for name in ('worklist', 'labels'):
        val.add_argument('--'+name, required=True)
    pair = sub.add_parser('compare')
    for name in ('private-dir', 'worklist-a', 'worklist-b', 'labels-a', 'labels-b', 'out-dir'):
        pair.add_argument('--'+name, required=True)
    a = p.parse_args(argv)
    try:
        inputs = {}
        def read(path):
            path = Path(path).resolve(); raw = path.read_bytes()
            if path in inputs and raw != inputs[path]:
                raise ValueError('input drift')
            inputs[path] = raw; return raw
        def unchanged():
            if any(path.read_bytes() != raw for path, raw in inputs.items()):
                raise ValueError('input changed')
        if a.cmd == 'prepare':
            dirs = [Path(x).resolve() for x in (a.rater_a_dir, a.rater_b_dir, a.private_dir)]
            new_dirs(dirs)
            raw = read(a.corpus); works, audit = make_packets(strict_json(raw.decode('utf8')), sha(raw))
            unchanged()
            for d in dirs: d.mkdir()
            hashes = []; files = {}
            for i in range(2):
                write(dirs[i]/'worklist.json', works[i]); h = sha((dirs[i]/'worklist.json').read_bytes()); hashes.append(h)
                write(dirs[i]/'labels-template.json', template(works[i], h))
                files[str(i)] = {n: sha((dirs[i]/n).read_bytes()) for n in ('worklist.json', 'labels-template.json')}
            audit['worklist_sha256'] = hashes
            write(dirs[2]/'keymap.json', audit)
            unchanged()
            write(dirs[2]/'complete.json', dict(keymap_sha256=sha((dirs[2]/'keymap.json').read_bytes()), packets=files))
            print(json.dumps(dict(cases=len(works[0]['rows']), packets=2, labels_completed=0, release_authorized=False)))
        elif a.cmd == 'validate':
            work = read(a.worklist); labels = strict_json(read(a.labels).decode('utf8'))
            result = validate_labels(work, labels); unchanged(); print(json.dumps(result))
        else:
            out = Path(a.out_dir).resolve(); new_dirs([out])
            private = Path(a.private_dir).resolve()
            workpaths = [Path(x).resolve() for x in (a.worklist_a, a.worklist_b)]
            if any(out.is_relative_to(d) for d in [private, *(p.parent for p in workpaths)]):
                raise ValueError('comparison output must be outside private/work packets')
            audit_raw = read(private/'keymap.json'); complete = strict_json(read(private/'complete.json').decode('utf8'))
            if complete.get('keymap_sha256') != sha(audit_raw):
                raise ValueError('private completion mismatch')
            works = [read(p) for p in workpaths]
            labels = [strict_json(read(p).decode('utf8')) for p in (a.labels_a, a.labels_b)]
            for i, work in enumerate(works):
                if complete['packets'][str(i)]['worklist.json'] != sha(work):
                    raise ValueError('packet completion mismatch')
            report = compare(strict_json(audit_raw.decode('utf8')), works, labels)
            report['source_fingerprints'] = {str(p):sha(raw) for p, raw in inputs.items()}
            unchanged(); out.mkdir()
            write(out/'comparison.json', report)
            # Keep exact initial submissions separately, never rewrite them into a consensus.
            for i, path in enumerate((a.labels_a, a.labels_b)):
                with (out/f'initial-labels-{i+1}.json').open('xb') as f:
                    f.write(inputs[Path(path).resolve()])
            unchanged()
            write(out/'complete.json', {n:sha((out/n).read_bytes()) for n in ('comparison.json','initial-labels-1.json','initial-labels-2.json')})
            print(json.dumps({k:report[k] for k in ('total_cases','jointly_labeled','agreements','disagreements','clarification_cases','independence_verified')}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print('Semantic annotation rejected: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
