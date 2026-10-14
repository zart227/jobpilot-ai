"""Ensure remediation checklist has only the expected live-Kwork gaps."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "remediation-checklists.md"
STATUS_SCRIPT = ROOT / "scripts" / "remediation_checklist_status.py"


def test_checklist_open_items_are_live_kwork_only() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    open_lines = [line for line in text.splitlines() if line.startswith("- [ ]")]
    assert len(open_lines) == 0


def test_remediation_checklist_status_script_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Checklist gate OK" in result.stdout
    assert re.search(r"115 closed, 0 open", result.stdout)
