import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_checklist_report_counts_actual_open_and_closed() -> None:
    text = (ROOT / "docs/remediation-plan.md").read_text()
    closed = len(re.findall(r"^- \[x\]", text, re.MULTILINE))
    opened = len(re.findall(r"^- \[ \]", text, re.MULTILINE))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/remediation_checklist_report.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    tail = result.stdout.split("Итого")[-1]
    assert f"**{closed}**" in tail and f"**{opened}**" in tail
