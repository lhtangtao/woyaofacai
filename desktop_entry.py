"""Frozen application entry, with no source database or machine-specific path."""
from portfolio.app import main

if __name__ == "__main__":
    raise SystemExit(main())
