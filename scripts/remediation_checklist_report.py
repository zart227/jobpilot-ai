#!/usr/bin/env python3
"""Print per-section checklist stats for remediation-progress / reviews."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "remediation-checklists.md"


def main() -> int:
    text = CHECKLIST.read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    total_open = 0
    total_closed = 0
    print("| Секция | Закрыто | Open |")
    print("| ------ | ------- | ---- |")
    for part in parts:
        title = part.split("\n", 1)[0].strip()
        if title.startswith("Журнал"):
            break
        block = part
        closed = len(re.findall(r"^- \[x\]", block, re.MULTILINE))
        open_ = len(re.findall(r"^- \[ \]", block, re.MULTILINE))
        if closed == 0 and open_ == 0:
            continue
        total_open += open_
        total_closed += closed
        print(f"| {title[:40]} | {closed} | {open_} |")
    print(f"| **Итого** | **{total_closed}** | **{total_open}** |")
    return 0 if total_open <= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
