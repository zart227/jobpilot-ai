import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_checklist_report_runs_and_shows_zero_open() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "remediation_checklist_report.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    tail = result.stdout.split("Итого")[-1]
    assert "**115**" in tail and "**0**" in tail
    assert "Итого" in result.stdout
