#!/usr/bin/env python3
"""Validate owner signoff file before marking live Kwork checklist items [x]."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNOFF = Path(
    os.environ.get("JOBPILOT_LIVE_SIGNOFF_PATH", str(ROOT / "data" / "live_kwork_signoff.json"))
)
REQUIRED_STRINGS = (
    "approved_by",
    "approved_at",
    "kwork_order_id",
    "send_attempt_id",
)
REQUIRED_BOOLS = ("snapshot_verified", "ui_matches_db")


def main() -> int:
    if not SIGNOFF.is_file():
        print(
            f"Missing {SIGNOFF}\n"
            f"Copy from data/live_kwork_signoff.json.example and fill after live test.",
            file=sys.stderr,
        )
        return 1

    try:
        data = json.loads(SIGNOFF.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    for key in REQUIRED_STRINGS:
        value = data.get(key)
        if not value or not str(value).strip():
            errors.append(f"missing or empty: {key}")
    for key in REQUIRED_BOOLS:
        if data.get(key) is not True:
            errors.append(f"{key} must be true after verification")

    if errors:
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Live signoff file OK. Checklist is already 115/115 unless you re-run live; optional journal:")
    print("  ./scripts/record_live_kwork_acceptance.sh", data["kwork_order_id"], data["send_attempt_id"], '"verified"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
