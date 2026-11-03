#!/usr/bin/env python3
"""Render reviewed audit evidence; file existence never proves remediation completion."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "audit-2026-10-14-evidence.json"


def render_markdown() -> str:
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    lines = [
        f"# Audit → remediation matrix — historical findings as of {data.get('implementation_progress', {}).get('date', data['date'])}",
        "",
        "Reviewed source: [audit-2026-10-14.md](audit-2026-10-14.md); historical baseline: [audit-2026-10-13.md](audit-2026-10-13.md).",
        "",
        "Historical finding statuses below are dated and may be stale; see [current audit](audit-2026-11-03-completion.md) and the checklist for current work. Rendering does not rerun tests or approve release.",
        "",
        "| # | P | Статус | Находка | Остаток / evidence |",
        "| - | - | ------ | ------- | ----------------- |",
    ]
    counts: dict[str, int] = {}
    for f in data["findings"]:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
        lines.append(
            f"| {f['num']} | {f['priority']} | {f['status']} | {f['title']} | {f['notes']} |"
        )
    checklist = data["checklist"]
    checklist_text = (ROOT / "docs" / "remediation-checklists.md").read_text(encoding="utf-8")
    current_closed = len(re.findall(r"^- \[x\].*\*\*C\d{3}\*\*", checklist_text, re.MULTILINE))
    current_open = len(re.findall(r"^- \[ \].*\*\*C\d{3}\*\*", checklist_text, re.MULTILINE))
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Findings: **{len(data['findings'])}**",
            f"- By status: {counts}",
            f"- Historical checklist snapshot: {checklist['closed']}/{checklist['total']} confirmed, {checklist['open']} open.",
            f"- Current checklist markers (not independent evidence): {current_closed}/{current_closed + current_open} closed, {current_open} open.",
            "- Release not accepted. Live/staging criteria cannot be replaced by simulated evidence.",
            "- Review evidence is dated; update it when code or accepted behavior changes.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    report = render_markdown()
    if args.write:
        args.write.write_text(report, encoding="utf-8")
        print(f"Wrote {args.write}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
