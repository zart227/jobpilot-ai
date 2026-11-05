#!/usr/bin/env python3
"""Release gate: open criteria block release; do not require dishonest closure in tests."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "remediation-plan.md"


def main() -> int:
    text = CHECKLIST.read_text(encoding="utf-8")
    open_items = [line[6:].strip() for line in text.splitlines() if line.startswith("- [ ]")]
    closed = len(re.findall(r"^- \[x\]", text, re.MULTILINE))
    print(f"remediation-plan: {closed} closed, {len(open_items)} open")
    for item in open_items:
        print(f"  OPEN: {item[:120]}")
    maximum = int(os.environ.get("REMEDIATION_MAX_OPEN", "0"))
    if len(open_items) > maximum:
        print(
            f"Release blocked: {len(open_items)} open criteria, allowed {maximum}.", file=sys.stderr
        )
        return 1
    if open_items:
        print(
            "Checklist gate threshold satisfied; open criteria remain, this is not release approval."
        )
    else:
        print("Checklist gate OK; separate runtime/live evidence is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
