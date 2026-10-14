#!/usr/bin/env sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python -m pip install --upgrade pip
pip install -r requirements-lock.txt
pip install --no-deps -e .
pip check

echo "Lockfile install OK"
