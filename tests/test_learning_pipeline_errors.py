"""Learning agent uses shared pipeline error classification (C054 partial)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agents import learning_agent
from app.config import Settings
from app.llm.errors import LLMErrorKind, LLMServiceError


async def test_learning_timeout_is_retryable_processing_error(monkeypatch):
    llm = SimpleNamespace(
        complete=AsyncMock(
            side_effect=LLMServiceError(LLMErrorKind.TIMEOUT, "timeout", True)
        )
    )
    monkeypatch.setattr(learning_agent, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(learning_agent, "get_simple_llm_provider", lambda: llm)
    agent = learning_agent.LearningAgent()
    agent._gather_stats = AsyncMock(return_value={})
    agent._persist_weights = AsyncMock()
    result = await agent.run({"outcome_status": "replied"})
    assert result["pipeline_processing_error"] is True
    assert result["processing_retryable"] is True
    assert result["processing_stage"] == "learning"
    assert result["processing_error_kind"] == LLMErrorKind.TIMEOUT.value
