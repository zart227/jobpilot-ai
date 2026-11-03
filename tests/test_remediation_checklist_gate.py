"""An honest open checklist must block release without failing report validation."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "remediation-checklists.md"
STATUS_SCRIPT = ROOT / "scripts" / "remediation_checklist_status.py"


def test_release_gate_matches_actual_open_criteria() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    closed = len(re.findall(r"^- \[x\]", text, re.MULTILINE))
    opened = len(re.findall(r"^- \[ \]", text, re.MULTILINE))
    env = dict(os.environ, REMEDIATION_MAX_OPEN="0")
    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if opened else 0)
    assert f"{closed} closed, {opened} open" in result.stdout
    if opened:
        assert "Release blocked" in result.stderr


def test_report_threshold_does_not_claim_release_approval() -> None:
    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT)],
        cwd=ROOT,
        env=dict(os.environ, REMEDIATION_MAX_OPEN="115"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    if "OPEN:" in result.stdout:
        assert "not release approval" in result.stdout
