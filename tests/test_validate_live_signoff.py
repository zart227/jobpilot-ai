import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_live_signoff.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_live_signoff", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_live_signoff_missing_file(tmp_path, monkeypatch):
    mod = _load_validator()
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("JOBPILOT_LIVE_SIGNOFF_PATH", str(missing))
    monkeypatch.setattr(mod, "SIGNOFF", missing)
    assert mod.main() == 1


def test_validate_live_signoff_accepts_complete_file(tmp_path, monkeypatch):
    mod = _load_validator()
    signoff_path = tmp_path / "live_kwork_signoff.json"
    signoff_path.write_text(
        json.dumps(
            {
                "approved_by": "owner",
                "approved_at": "2026-10-14",
                "kwork_order_id": "99",
                "send_attempt_id": "00000000-0000-0000-0000-000000000001",
                "snapshot_verified": True,
                "ui_matches_db": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBPILOT_LIVE_SIGNOFF_PATH", str(signoff_path))
    monkeypatch.setattr(mod, "SIGNOFF", signoff_path)
    assert mod.main() == 0
