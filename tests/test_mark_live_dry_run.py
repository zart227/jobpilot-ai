import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mark_live_checklist_complete.sh"


def test_mark_live_dry_run_does_not_modify_checklist(live_signoff_artifacts):
    path, _ = live_signoff_artifacts
    checklist = ROOT / "docs/remediation-plan.md"
    before = checklist.read_bytes()
    result = subprocess.run([str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "JOBPILOT_LIVE_SIGNOFF_PATH": str(path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "no checklist files changed" in result.stdout
    assert checklist.read_bytes() == before


def test_simulated_signoff_cannot_close_live_items(tmp_path):
    path = tmp_path / "signoff.json"
    path.write_text('{"schema_version":2,"mode":"simulation","ui_matches_db":true}')
    checklist = ROOT / "docs/remediation-plan.md"
    before = checklist.read_bytes()
    result = subprocess.run([str(SCRIPT)],
        cwd=ROOT,
        env={**os.environ, "JOBPILOT_LIVE_SIGNOFF_PATH": str(path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert checklist.read_bytes() == before
