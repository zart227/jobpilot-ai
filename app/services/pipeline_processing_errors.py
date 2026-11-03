"""Map stage exceptions to pipeline retry/filter outcomes."""

from __future__ import annotations

import asyncio

from app.llm.errors import LLMErrorKind, LLMServiceError, classify_llm_error
from app.schemas.llm_outputs import is_validation_error


def pipeline_error_from_exception(exc: Exception, *, stage: str) -> dict:
    """Build graph state for a failed stage without marking the job filtered."""
    if isinstance(exc, LLMServiceError):
        retryable = exc.retryable
        kind = exc.kind.value
    else:
        classified = classify_llm_error(exc)
        if classified.kind != LLMErrorKind.UNKNOWN:
            retryable = classified.retryable
            kind = classified.kind.value
        elif isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
            retryable = True
            kind = LLMErrorKind.TIMEOUT.value if isinstance(exc, asyncio.TimeoutError) else LLMErrorKind.NETWORK.value
        else:
            retryable = not is_validation_error(exc)
            kind = "validation" if not retryable else LLMErrorKind.UNKNOWN.value

    return {
        "pipeline_processing_error": True,
        "processing_retryable": retryable,
        "processing_stage": stage,
        "processing_error_kind": kind,
        "error": str(exc),
    }
