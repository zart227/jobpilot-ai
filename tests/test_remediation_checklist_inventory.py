"""Validate stable acceptance IDs and truthful totals, not mandatory completion."""

import re
from pathlib import Path

CHECKLIST = Path(__file__).resolve().parents[1] / "docs" / "remediation-checklists.md"


def test_checklist_inventory_preserves_original_115_unique_criteria() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    lines = re.findall(r"^- \[[ x]\].*$", text, re.MULTILINE)
    assert len(lines) == 115
    ids = [re.search(r"\bC\d{3}\b", line).group() for line in lines]
    assert len(set(ids)) == 115
    assert set(ids) == {f"C{i:03d}" for i in range(1, 116)}


def test_checklist_header_documents_actual_ratio() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    closed = len(re.findall(r"^- \[x\]", text, re.MULTILINE))
    opened = len(re.findall(r"^- \[ \]", text, re.MULTILINE))
    header = text.split("## 0.")[0]
    assert f"{closed}/115" in header
    assert f"{opened} открыто" in header
