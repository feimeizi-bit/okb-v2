"""Build a private offline candidate bundle using explicitly supplied installed dependencies."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from portable.bundle import build


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-dir', required=True)
    parser.add_argument('--out-dir', required=True)
    arguments = parser.parse_args(argv)
    try:
        manifest = build(ROOT, arguments.dependency_dir, arguments.out_dir)
        print(json.dumps(dict(files=len(manifest['files']), dependencies=len(manifest['dependencies']),
                              accuracy_validated=False, fresh_openkylin_accepted=False, publication_authorized=False)))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Portable build rejected: ' + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
