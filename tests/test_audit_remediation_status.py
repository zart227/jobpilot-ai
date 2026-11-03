import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_remediation_status.py"


def test_audit_remediation_status_lists_23_findings() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "| 23 |" in result.stdout
    assert "Findings: **23**" in result.stdout


def test_audit_matrix_can_write_file(tmp_path: Path) -> None:
    out = tmp_path / "matrix.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--write", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert out.is_file()
    assert "audit-2026-10-13" in out.read_text(encoding="utf-8")


def test_matrix_separates_current_checklist_count_from_historical_snapshot() -> None:
    import re

    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    checklist = (ROOT / "docs" / "remediation-checklists.md").read_text(encoding="utf-8")
    closed = len(re.findall(r"^- \[x\].*\*\*C\d{3}\*\*", checklist, re.MULTILINE))
    opened = len(re.findall(r"^- \[ \].*\*\*C\d{3}\*\*", checklist, re.MULTILINE))
    assert f"Current checklist markers (not independent evidence): {closed}/{closed + opened} closed, {opened} open." in result.stdout
    assert "Historical checklist snapshot:" in result.stdout
