"""C054: LLM timeout/rate limit must become processing errors, not filtered jobs."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.errors import LLMErrorKind, LLMServiceError
from app.services.pipeline_processing_errors import pipeline_error_from_exception

ROOT = Path(__file__).resolve().parents[1]

PIPELINE_LLM_MODULES = [
    "app/agents/filter_agent.py",
    "app/agents/scoring_agent.py",
    "app/agents/proposal_agent.py",
    "app/agents/chat_agent.py",
    "app/agents/learning_agent.py",
    "app/services/conversation_summary.py",
]


def test_pipeline_llm_modules_use_shared_error_mapper():
    missing = []
    for rel in PIPELINE_LLM_MODULES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "pipeline_error_from_exception" not in text:
            missing.append(rel)
    assert not missing, f"missing pipeline_error_from_exception: {missing}"


@pytest.mark.parametrize(
    "exc,kind",
    [
        (TimeoutError(), LLMErrorKind.TIMEOUT),
        (LLMServiceError(LLMErrorKind.RATE_LIMIT, "rl", retryable=True), LLMErrorKind.RATE_LIMIT),
    ],
)
def test_retryable_llm_failures_are_processing_errors(exc, kind):
    payload = pipeline_error_from_exception(exc, stage="filter")
    assert payload["pipeline_processing_error"] is True
    assert payload["processing_retryable"] is True
    assert payload["processing_error_kind"] == kind.value
