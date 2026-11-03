import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_live_signoff.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_live_signoff", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_live_signoff_missing_file(tmp_path, monkeypatch):
    mod = _load_validator()
    monkeypatch.setattr(mod, "SIGNOFF", tmp_path / "nope.json")
    assert mod.main([]) == 1


def test_legacy_boolean_signoff_is_rejected(tmp_path, monkeypatch):
    mod = _load_validator()
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps({"approved_by": "owner", "ui_matches_db": True, "snapshot_verified": True})
    )
    monkeypatch.setattr(mod, "SIGNOFF", path)
    assert mod.main([]) == 1


def test_live_signoff_checks_db_before_acceptance(live_signoff_artifacts, monkeypatch):
    mod = _load_validator()
    path, data = live_signoff_artifacts
    monkeypatch.setattr(mod, "SIGNOFF", path)
    verify = AsyncMock()
    monkeypatch.setattr(mod, "verify_database", verify)
    assert mod.main([]) == 0
    verify.assert_awaited_once_with(data)


def test_artifacts_only_is_not_live_acceptance(live_signoff_artifacts, monkeypatch, capsys):
    mod = _load_validator()
    path, _ = live_signoff_artifacts
    monkeypatch.setattr(mod, "SIGNOFF", path)
    verify = AsyncMock()
    monkeypatch.setattr(mod, "verify_database", verify)
    assert mod.main(["--artifacts-only"]) == 0
    verify.assert_not_called()
    assert "not live acceptance" in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [
        "simulation",
        "missing_ui",
        "tampered_ui",
        "wrong_attempt",
        "missing_authorization",
        "outside_path",
    ],
)
def test_invalid_evidence_rejected(live_signoff_artifacts, change):
    mod = _load_validator()
    path, data = live_signoff_artifacts
    if change == "simulation":
        data["mode"] = "simulation"
    elif change == "missing_ui":
        (path.parent / "ui.png").unlink()
    elif change == "tampered_ui":
        (path.parent / "ui.png").write_bytes(b"tampered")
    elif change == "wrong_attempt":
        data["send_attempt_id"] = "00000000-0000-0000-0000-000000000099"
    elif change == "missing_authorization":
        data.pop("authorization_reference")
    else:
        data["ui_evidence"]["path"] = "../outside.png"
    with pytest.raises(ValueError):
        mod.validate_artifacts(data, path.parent)


def test_db_failure_does_not_print_sensitive_exception(live_signoff_artifacts, monkeypatch, capsys):
    mod = _load_validator()
    path, _ = live_signoff_artifacts
    monkeypatch.setattr(mod, "SIGNOFF", path)
    monkeypatch.setattr(
        mod, "verify_database", AsyncMock(side_effect=RuntimeError("PASSWORD=secret"))
    )
    assert mod.main([]) == 1
    assert "PASSWORD" not in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["simulation", "wrong_snapshot", "wrong_order", "unconfirmed"])
async def test_database_evidence_rejects_mismatched_or_simulated_attempt(
    live_signoff_artifacts,
    monkeypatch,
    problem,
):
    from types import SimpleNamespace

    from app.db import session as db_module
    from app.db.models import SendAttempt

    mod = _load_validator()
    _, data = live_signoff_artifacts
    attempt = SimpleNamespace(
        status="confirmed_sent",
        kind="proposal",
        job_id="job",
        platform_response={},
        snapshot_content="actual approved text",
    )
    job = SimpleNamespace(
        platform="kwork", external_id="kwork-42", url="https://kwork.ru/projects/42/view"
    )
    if problem == "simulation":
        attempt.platform_response = {"dry_run": True}
    elif problem == "wrong_snapshot":
        attempt.snapshot_content = "other content"
    elif problem == "wrong_order":
        job.external_id, job.url = "kwork-99", "https://kwork.ru/projects/99/view"
    else:
        attempt.status = "sending"
    session = AsyncMock()
    session.get.side_effect = lambda model, _id: attempt if model is SendAttempt else job
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(db_module, "AsyncSessionLocal", lambda: context)
    monkeypatch.setattr(db_module, "engine", SimpleNamespace(dispose=AsyncMock()))
    with pytest.raises(ValueError):
        await mod.verify_database(data)
