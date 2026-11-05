#!/usr/bin/env sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
  PIP="$ROOT/.venv/bin/pip"
else
  PY=python3
  PIP=pip3
fi

"$PY" -m pip install --upgrade pip
"$PIP" install -r requirements-lock.txt
"$PIP" install --no-deps -e .
"$PIP" check

echo "Lockfile install OK"
