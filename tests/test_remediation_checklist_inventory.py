import re
from pathlib import Path

CHECKLIST = Path(__file__).resolve().parents[1] / "docs" / "remediation-checklists.md"


def test_checklist_inventory_115_closed_0_open() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    closed = len(re.findall(r"^- \[x\]", text, re.MULTILINE))
    open_ = len(re.findall(r"^- \[ \]", text, re.MULTILINE))
    assert closed == 115
    assert open_ == 0
    assert closed + open_ == 115


def test_checklist_header_documents_ratio() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    assert "115/115" in text.split("## 0.")[0]
