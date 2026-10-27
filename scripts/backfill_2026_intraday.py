"""Compatibility entry: all historical owners, shared prices, new derived database.

No legacy scope-key rewrite and no 288-point completeness heuristic.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from portfolio.derived import main

if __name__=='__main__':
    raise SystemExit(main(['rebuild',*sys.argv[1:]]))
