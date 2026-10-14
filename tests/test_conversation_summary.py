import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat_agent import build_chat_user_prompt
from app.db.models import Job
from app.services.conversation_summary import (
    RECENT_TURNS,
    _older_turns_fingerprint,
    build_conversation_context,
    summarize_turns,
)


def test_build_chat_user_prompt_uses_conversation_context() -> None:
    prompt = build_chat_user_prompt(
        {"title": "Бот", "description": "Описание"},
        "Наш отклик",
        "Какой срок?",
        conversation_context="Клиент спрашивал срок, мы ответили 5 дней.",
    )
    assert "Клиент спрашивал срок" in prompt
    assert "История переписки" in prompt


@pytest.mark.asyncio
async def test_build_conversation_context_short_history_verbatim() -> None:
    job = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="1",
        title="Test",
        description="Desc",
    )
    turns = [
        {"role": "client", "text": "Привет"},
        {"role": "freelancer", "text": "Здравствуйте"},
    ]
    context = await build_conversation_context(None, job, turns)
    assert "Привет" in context
    assert "Здравствуйте" in context
    assert "Кратко о более ранней" not in context


@pytest.mark.asyncio
async def test_build_conversation_context_uses_cache() -> None:
    job = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="1",
        title="Test",
        description="Desc",
        raw_data={
            "inbox_conversation_summary": {
                "older_turn_count": 3,
                "older_turn_fingerprint": "",
                "text": "Обсудили срок и бюджет.",
            }
        },
    )
    turns = [
        {"role": "client", "text": "Привет"},
        {"role": "freelancer", "text": "Здравствуйте"},
        {"role": "client", "text": "Срок?"},
        {"role": "freelancer", "text": "5 дней"},
        {"role": "client", "text": "Ок, а цена?"},
    ]
    older_fp = _older_turns_fingerprint(turns[:-RECENT_TURNS])
    job.raw_data["inbox_conversation_summary"]["older_turn_fingerprint"] = older_fp
    with patch(
        "app.services.conversation_summary.summarize_turns",
        new=AsyncMock(return_value="should not be called"),
    ) as mock_summarize:
        context = await build_conversation_context(None, job, turns)

    mock_summarize.assert_not_called()
    assert "Обсудили срок и бюджет." in context
    assert "Ок, а цена?" in context
    assert "5 дней" in context


@pytest.mark.asyncio
async def test_build_conversation_context_summarizes_and_saves() -> None:
    job = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="1",
        title="Test",
        description="Desc",
        raw_data={},
    )
    turns = [
        {"role": "client", "text": "Привет"},
        {"role": "freelancer", "text": "Здравствуйте"},
        {"role": "client", "text": "Срок?"},
        {"role": "freelancer", "text": "5 дней"},
        {"role": "client", "text": "Ок"},
    ]
    session = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.services.conversation_summary.summarize_turns",
        new=AsyncMock(return_value="Клиент уточнил срок, мы назвали 5 дней."),
    ):
        context = await build_conversation_context(session, job, turns)

    assert "Клиент уточнил срок" in context
    assert "Ок" in context
    session.commit.assert_awaited_once()
    cached = (job.raw_data or {}).get("inbox_conversation_summary", {})
    assert cached.get("older_turn_count") == len(turns) - RECENT_TURNS
    assert cached.get("text") == "Клиент уточнил срок, мы назвали 5 дней."


@pytest.mark.asyncio
async def test_summarize_turns_truncates_long_output() -> None:
    turns = [{"role": "client", "text": "Длинный вопрос"}]
    long_summary = "x" * 1000
    with patch(
        "app.services.conversation_summary.get_simple_llm_provider",
    ) as mock_provider:
        mock_provider.return_value.complete = AsyncMock(return_value=long_summary)
        result = await summarize_turns(turns)

    assert len(result) <= 700
    assert result.endswith("...")
