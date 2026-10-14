import pytest

from app.schemas.llm_outputs import parse_chat_output


def test_parse_chat_output_valid() -> None:
    raw = '{"intent": "question", "reply": "Срок 5 дней."}'
    parsed = parse_chat_output(raw)
    assert parsed.intent == "question"
    assert "5 дней" in parsed.reply


def test_parse_chat_output_rejects_invalid_intent() -> None:
    with pytest.raises(Exception):
        parse_chat_output('{"intent": "spam", "reply": "hi"}')


def test_parse_chat_output_rejects_empty_reply() -> None:
    with pytest.raises(Exception):
        parse_chat_output('{"intent": "other", "reply": ""}')
