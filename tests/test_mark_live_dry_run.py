import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mark_live_checklist_complete.sh"


def test_mark_live_dry_run_with_signoff_env(tmp_path):
    signoff_path = tmp_path / "live_kwork_signoff.json"
    signoff_path.write_text(
        json.dumps(
            {
                "approved_by": "owner",
                "approved_at": "2026-10-14",
                "kwork_order_id": "42",
                "send_attempt_id": "00000000-0000-0000-0000-000000000099",
                "snapshot_verified": True,
                "ui_matches_db": True,
            }
        ),
        encoding="utf-8",
    )
    env = {
        **dict(__import__("os").environ),
        "JOBPILOT_LIVE_SIGNOFF_PATH": str(signoff_path),
    }
    result = subprocess.run(
        [str(SCRIPT), "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "42" in result.stdout
    assert "00000000-0000-0000-0000-000000000099" in result.stdout
