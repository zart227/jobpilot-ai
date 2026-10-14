#!/usr/bin/env python3
"""Report open remediation checklist items (expect only live Kwork until owner signs off)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "remediation-checklists.md"
MAX_ALLOWED_OPEN = int(__import__("os").environ.get("REMEDIATION_MAX_OPEN", "0"))


def main() -> int:
    text = CHECKLIST.read_text(encoding="utf-8")
    open_items: list[str] = []
    for line in text.splitlines():
        if line.startswith("- [ ]"):
            open_items.append(line.removeprefix("- [ ] ").strip())

    closed = len(re.findall(r"^- \[x\]", text, re.MULTILINE))
    print(f"remediation-checklists: {closed} closed, {len(open_items)} open")
    for item in open_items:
        print(f"  OPEN: {item[:120]}{'...' if len(item) > 120 else ''}")

    if len(open_items) > MAX_ALLOWED_OPEN:
        print(
            f"\nUnexpected: more than {MAX_ALLOWED_OPEN} open items "
            "(expected only live Kwork acceptance).",
            file=sys.stderr,
        )
        return 1
    if open_items and not all(
        "Kwork" in item or "live" in item.lower() or "разрешение" in item.lower()
        for item in open_items
    ):
        print("\nOpen items should be live Kwork only.", file=sys.stderr)
        return 1
    if not open_items and MAX_ALLOWED_OPEN > 0:
        print("Checklist gate OK (fully closed).")
        return 0
    print("Checklist gate OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
