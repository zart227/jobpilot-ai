"""mark_live_checklist_complete.sh is only run by owner with real signoff — smoke import paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mark_live_script_exists_and_is_executable() -> None:
    script = ROOT / "scripts" / "mark_live_checklist_complete.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111
