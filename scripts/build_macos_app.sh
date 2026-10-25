#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/build_macos_app.py
