#!/usr/bin/env sh
# Automatic owner authorization/live closure is not possible.
set -eu
echo "Automatic live acceptance is disabled: dry-run cannot grant owner authorization." >&2
echo "Use isolated tests, then owner-reviewed live evidence (docs/block-a-access-and-evidence.md)." >&2
exit 1
