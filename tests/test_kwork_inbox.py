from pathlib import Path

from app.services.kwork_api import clean_kwork_message_text
from app.services.kwork_inbox import (
    ClientInboxMessage,
    InboxState,
    _is_from_client,
    _message_key,
    format_client_message_alert,
)


def test_clean_kwork_message_text() -> None:
    assert clean_kwork_message_text("<b>Hello</b><br/>World") == "Hello\nWorld"


def test_message_key_stable() -> None:
    msg = {"MID": 12345, "message": "Привет"}
    assert _message_key(99, msg) == "99:12345"


def test_is_from_client_skips_own_messages() -> None:
    msg = {"from_username": "seller", "message": "Мой ответ", "type": "text"}
    assert _is_from_client(msg, "seller", 1) is False


def test_is_from_client_accepts_client() -> None:
    msg = {"from_username": "buyer123", "message": "Здравствуйте", "type": "text"}
    assert _is_from_client(msg, "seller", 1) is True


def test_is_from_client_skips_system() -> None:
    msg = {"from_username": "buyer123", "message": "Заказ создан", "type": "order_created"}
    assert _is_from_client(msg, "seller", 1) is False


def test_inbox_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "inbox.json"
    state = InboxState(seen_keys=["1:100"], my_username="seller")
    state.mark_seen("2:200")
    state.save(path)

    loaded = InboxState.load(path)
    assert loaded.my_username == "seller"
    assert loaded.is_seen("1:100")
    assert loaded.is_seen("2:200")
    assert not loaded.is_seen("3:300")


def test_format_client_message_alert() -> None:
    msg = ClientInboxMessage(
        user_id=1,
        username="buyer",
        message_id="42",
        text="Нужен срок выполнения",
        timestamp=0,
        dialog={},
        raw={},
    )
    text = format_client_message_alert(
        msg,
        chat_intent="question",
        chat_reply="Срок 3-5 дней.",
    )
    assert "buyer" in text
    assert "Черновик ответа" in text
    assert "Черновик ответа" in text
