from datetime import date

import pytest

from app.config import Settings
from app.services import kwork_pause as pause_mod
from app.services.kwork_pause import (
    KworkPauseState,
    is_kwork_connects_pause_active,
    is_kwork_inbox_paused,
    is_kwork_manual_pause_active,
    is_kwork_offers_paused,
    save_pause_state,
)


@pytest.fixture(autouse=True)
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pause_mod, "_local_today", lambda _s: date(2026, 7, 7))


def test_connects_pause_blocks_offers_not_inbox_by_default(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        _env_file=None,
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_BLOCK_INBOX_ON_CONNECTS=False,
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    save_pause_state(
        KworkPauseState(paused=True, paused_until=date(2026, 7, 10)),
        settings,
    )
    assert is_kwork_connects_pause_active(settings) is True
    assert is_kwork_offers_paused(settings) is True
    assert is_kwork_inbox_paused(settings) is False


def test_manual_pause_blocks_inbox(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=False,
        KWORK_PAUSE_UNTIL="2026-07-10",
        KWORK_PAUSE_STATE_FILE=str(tmp_path / "pause.json"),
    )
    assert is_kwork_manual_pause_active(settings) is True
    assert is_kwork_inbox_paused(settings) is True
