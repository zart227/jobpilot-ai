import uuid
from types import SimpleNamespace

from app.config import Settings
from app.db.models import TelegramPending
from app.telegram.access import is_authorized_telegram, pending_matches_callback


def test_is_authorized_telegram_requires_chat_and_optional_user() -> None:
    settings = Settings(
        TELEGRAM_ADMIN_CHAT_ID="100",
        TELEGRAM_ADMIN_USER_ID="42",
    )
    assert is_authorized_telegram(100, 42, settings) is True
    assert is_authorized_telegram(100, 99, settings) is False
    assert is_authorized_telegram(101, 42, settings) is False


def test_is_authorized_telegram_chat_only_when_user_not_configured() -> None:
    settings = Settings(TELEGRAM_ADMIN_CHAT_ID="100", TELEGRAM_ADMIN_USER_ID="")
    assert is_authorized_telegram(100, None, settings) is True
    assert is_authorized_telegram(100, 7, settings) is True


def test_pending_matches_callback_checks_message_id() -> None:
    pending = TelegramPending(
        job_id=uuid.uuid4(),
        proposal_id=uuid.uuid4(),
        chat_id=100,
        message_id=55,
    )
    callback = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(id=100), message_id=55),
    )
    assert pending_matches_callback(pending, callback) is True

    stale = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(id=100), message_id=56),
    )
    assert pending_matches_callback(pending, stale) is False
