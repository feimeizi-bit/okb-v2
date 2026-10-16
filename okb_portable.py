"""Offline entry point for a sealed, repository-layout candidate bundle."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))

from portable.runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
