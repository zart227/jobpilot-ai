from pathlib import Path

from app.agents.chat_agent import build_chat_user_prompt, format_conversation_history
from app.services.kwork_api import clean_kwork_message_text
from app.services.kwork_conversation import (
    conversation_from_interactions,
    conversation_from_kwork_thread,
)
from app.services.kwork_inbox import (
    ClientInboxMessage,
    InboxState,
    _extract_project_id,
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


def test_format_client_message_alert_needs_context() -> None:
    msg = ClientInboxMessage(
        user_id=1,
        username="buyer",
        message_id="42",
        text="Вопрос",
        timestamp=0,
        dialog={},
        raw={},
    )
    text = format_client_message_alert(
        msg,
        needs_context=True,
        candidate_job_count=3,
    )
    assert "Нужен выбор заказа" in text


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


def test_extract_project_id_from_message_html() -> None:
    message = {
        "message": (
            'Тема: <a href="https://kwork.ru/projects/3228711">AI проект</a>'
        )
    }
    assert _extract_project_id(message, {}) == "3228711"


def test_build_chat_user_prompt_includes_job_proposal_and_client() -> None:
    prompt = build_chat_user_prompt(
        {
            "title": "Бот",
            "description": "Нужен Telegram-бот",
            "budget_min": 10000,
            "budget_max": 20000,
            "budget_currency": "RUB",
            "skills": ["Python"],
        },
        "Мы готовы сделать за 5 дней",
        "Какой срок?",
    )
    assert "Бот" in prompt
    assert "Наш отклик" in prompt
    assert "Мы готовы" in prompt
    assert "Какой срок" in prompt
    assert "История переписки" in prompt


def test_conversation_from_kwork_thread_roles_and_excludes_latest() -> None:
    messages = [
        {"from_username": "buyer", "message": "Привет", "time": 1, "type": "text"},
        {"from_username": "seller", "message": "Здравствуйте", "time": 2, "type": "text"},
        {"from_username": "buyer", "message": "Какой срок?", "time": 3, "type": "text"},
    ]
    history = conversation_from_kwork_thread(
        messages,
        my_username="seller",
        my_user_id=99,
        exclude_last_client_text="Какой срок?",
    )
    assert history == [
        {"role": "client", "text": "Привет"},
        {"role": "freelancer", "text": "Здравствуйте"},
    ]


def test_build_chat_user_prompt_includes_conversation_history() -> None:
    prompt = build_chat_user_prompt(
        {"title": "Бот", "description": "Описание"},
        "Наш отклик",
        "Какой срок?",
        conversation_history=[
            {"role": "client", "text": "Привет"},
            {"role": "freelancer", "text": "Здравствуйте"},
        ],
    )
    assert "Клиент: Привет" in prompt
    assert "Вы: Здравствуйте" in prompt
    assert format_conversation_history([]) == "(ранее сообщений не было)"


def test_conversation_from_interactions_excludes_latest_client() -> None:
    from app.db.models import Interaction

    rows = [
        Interaction(direction="inbound", message="Привет", channel="kwork"),
        Interaction(direction="outbound", message="Здравствуйте", channel="kwork"),
        Interaction(direction="inbound", message="Какой срок?", channel="kwork"),
    ]
    history = conversation_from_interactions(rows, exclude_last_client_text="Какой срок?")
    assert len(history) == 2
    assert history[-1]["role"] == "freelancer"
