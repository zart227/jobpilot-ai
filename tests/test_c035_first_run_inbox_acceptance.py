"""C035: first-run inbox seeds unread history; later polls use received capture."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="explicit isolated PostgreSQL required",
)


def _dialog(user_id: int, username: str = "buyer"):
    mid = uuid.uuid4().hex
    return {
        "username": username,
        "user_id": user_id,
        "unread": True,
        "unread_count": 1,
    }, {
        "MID": mid,
        "message": "Старое непрочитанное сообщение",
        "time": 1_700_000_000,
    }


@pytest.mark.asyncio
async def test_first_run_seeds_without_telegram_notify(monkeypatch, tmp_path):
    from app.config import Settings
    from app.db.models import InboundMessage
    from app.db.session import AsyncSessionLocal
    from app.services import kwork_inbox

    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    dialog, message = _dialog(user_id)
    state_file = tmp_path / "inbox_state.json"

    class FakeApi:
        async def get_actor(self):
            return {"username": "seller", "id": 1}

        async def get_dialogs(self):
            return [dialog]

        async def get_messages(self, username: str):
            assert username == "buyer"
            return [message]

        async def close(self):
            return None

    monkeypatch.setattr(kwork_inbox, "create_kwork_api", AsyncMock(return_value=FakeApi()))
    monkeypatch.setattr(kwork_inbox, "_retry_received_inbound", AsyncMock())
    process = AsyncMock(return_value=0)
    monkeypatch.setattr(kwork_inbox, "_process_and_notify", process)

    settings = Settings(
        _env_file=None,
        kwork_email="a@b.c",
        kwork_password="x",
        telegram_bot_token="token",
        telegram_admin_chat_id="1",
        kwork_inbox_state_file=str(state_file),
    )
    assert not state_file.is_file()

    result = await kwork_inbox.check_kwork_inbox(settings, bot=object())

    assert result.get("initialized") is True
    assert result.get("seeded") == 1
    assert result.get("notified") == 0
    process.assert_not_awaited()
    assert state_file.is_file()

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(InboundMessage).where(
                InboundMessage.platform == "kwork",
                InboundMessage.kwork_user_id == user_id,
            )
        )
        assert row is not None
        assert row.status == "seeded"


@pytest.mark.asyncio
async def test_after_first_run_unread_uses_received_capture(monkeypatch, tmp_path):
    from app.config import Settings
    from app.services import kwork_inbox

    user_id = int(uuid.uuid4().int % 800_000_000) + 200
    _, _message = _dialog(user_id, "buyer2")
    state_file = tmp_path / "inbox_state.json"
    state_file.write_text('{"seen": [], "identity_ambiguities": []}', encoding="utf-8")

    captured_modes: list[str | None] = []

    async def collect(api, state, *, my_username, my_user_id, capture_mode=None):
        captured_modes.append(capture_mode)
        return []

    class FakeApi:
        async def get_actor(self):
            return {"username": "seller", "id": 1}

        async def close(self):
            return None

    monkeypatch.setattr(kwork_inbox, "create_kwork_api", AsyncMock(return_value=FakeApi()))
    monkeypatch.setattr(kwork_inbox, "_retry_received_inbound", AsyncMock())
    monkeypatch.setattr(kwork_inbox, "_collect_new_messages", collect)
    monkeypatch.setattr(kwork_inbox, "_process_and_notify", AsyncMock(return_value=0))

    settings = Settings(
        _env_file=None,
        kwork_email="a@b.c",
        kwork_password="x",
        telegram_bot_token="token",
        telegram_admin_chat_id="1",
        kwork_inbox_state_file=str(state_file),
    )

    await kwork_inbox.check_kwork_inbox(settings, bot=object())

    assert captured_modes == ["received"]
