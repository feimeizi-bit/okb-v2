"""Strict offline maintainer regression; every private input and runtime is explicit."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from verification.offline import Inputs, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--source-root', default=str(ROOT))
    for name in ('dependency-dir', 'node-executable', 'legacy-paper', 'legacy-plan', 'legacy-run',
                 'legacy-baseline', 'semantic-corpus', 'out-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--timeout', type=int, default=300)
    arguments = parser.parse_args(argv)
    try:
        paths = {name: Path(getattr(arguments, name)).resolve() for name in
                 ('source_root', 'dependency_dir', 'node_executable', 'legacy_paper', 'legacy_plan',
                  'legacy_run', 'legacy_baseline', 'semantic_corpus')}
        summary = verify(Inputs(**paths, out=Path(arguments.out_dir).resolve(), timeout=arguments.timeout))
        print(json.dumps({key: value for key, value in summary.items() if key not in ('results', 'legacy_compatibility')}, ensure_ascii=False))
        return 0 if summary['passed'] else 1
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print('Offline maintainer verification rejected: ' + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
