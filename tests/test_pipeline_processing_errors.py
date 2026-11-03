"""Pipeline stage errors must not be stored as filtered jobs."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.llm.errors import LLMErrorKind, LLMServiceError
from app.services.pipeline_processing_errors import pipeline_error_from_exception

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


def test_timeout_maps_to_retryable_processing_error():
    payload = pipeline_error_from_exception(TimeoutError(), stage="filter")
    assert payload["processing_retryable"] is True
    assert payload["processing_error_kind"] == LLMErrorKind.TIMEOUT.value
    assert payload["pipeline_processing_error"] is True


def test_rate_limit_maps_to_retryable_processing_error():
    exc = LLMServiceError(LLMErrorKind.RATE_LIMIT, "too many", retryable=True)
    payload = pipeline_error_from_exception(exc, stage="scoring")
    assert payload["processing_retryable"] is True
    assert payload["processing_error_kind"] == LLMErrorKind.RATE_LIMIT.value


def test_validation_maps_to_non_retryable_processing_error():
    payload = pipeline_error_from_exception(ValidationError.from_exception_data("x", []), stage="filter")
    assert payload["processing_retryable"] is False
    assert payload["processing_error_kind"] == "validation"


async def test_filter_timeout_persists_retryable_not_filtered():
    from sqlalchemy import select

    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService
    from app.services.job_processing import try_claim_job
    from app.services.pipeline_processing_errors import pipeline_error_from_exception

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d", status="new",
        ))
        await session.commit()
    claim = await try_claim_job(job_id, owner="test")
    assert claim
    result = pipeline_error_from_exception(
        LLMServiceError(LLMErrorKind.TIMEOUT, "timeout", retryable=True),
        stage="filter",
    )
    result["relevance_reason"] = "timeout"
    await JobPipelineService().persist_pipeline_results(job_id, result, claim_token=claim.claim_token)
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id))
        assert job.status == "retryable_error"
        assert job.processing_next_attempt_at > datetime.now(UTC)
        assert job.raw_data.get("last_processing_error_kind") == LLMErrorKind.TIMEOUT.value
