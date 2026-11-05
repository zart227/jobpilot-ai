"""C115: LLM usage/cost ledger per pipeline stage without billing failed retries twice."""

import os
import uuid

import pytest

from app.db.models import Job
from app.llm.call_usage import LLMCallUsage
from app.services.pipeline_llm_ledger import (
    append_stage_log,
    persist_stage_log,
    summarize_usage_entries,
)

if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
    pytest.skip("integration DB required", allow_module_level=True)


def test_retry_same_stage_replaces_not_duplicates_usage() -> None:
    usage_a = LLMCallUsage("model-a", "openrouter", prompt_tokens=10, completion_tokens=5, cost_usd=0.0)
    usage_b = LLMCallUsage("model-b", "openrouter", prompt_tokens=12, completion_tokens=6, cost_usd=0.0)
    log = append_stage_log({}, "filter", "simple", usage=usage_a)
    log = append_stage_log({"llm_stage_log": log}, "filter", "simple", usage=usage_b)
    assert len(log) == 1
    assert log[0]["resolved_model"] == "model-b"
    summary = summarize_usage_entries(log)
    assert summary["total_prompt_tokens"] == 12
    assert summary["by_stage"]["filter"]["calls"] == 1


def test_persist_stage_log_writes_usage_summary() -> None:
    job = Job(platform="kwork", external_id="c115", title="T", description="d", raw_data={})
    usage = LLMCallUsage(
        "north-mini:free",
        "openrouter",
        prompt_tokens=100,
        completion_tokens=40,
        total_tokens=140,
        cost_usd=0.0,
    )
    persist_stage_log(
        job,
        {"llm_stage_log": append_stage_log({}, "proposal", "main", usage=usage)},
    )
    assert job.raw_data["llm_usage_summary"]["total_prompt_tokens"] == 100
    assert job.raw_data["llm_stage_log"][0]["resolved_model"] == "north-mini:free"


@pytest.mark.asyncio
async def test_collect_ops_metrics_sums_llm_usage() -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.ops_metrics import collect_ops_metrics

    async with AsyncSessionLocal() as session:
        job = Job(
            platform="kwork",
            external_id=f"c115-ops-{uuid.uuid4().int}",
            title="T",
            description="d",
            raw_data={
                "llm_stage_count": 2,
                "llm_usage_summary": {
                    "total_cost_usd": 0.001,
                    "total_prompt_tokens": 50,
                    "total_completion_tokens": 20,
                    "by_stage": {},
                },
            },
        )
        session.add(job)
        await session.commit()

        metrics = await collect_ops_metrics(session)
    assert metrics["llm"]["total_prompt_tokens"] >= 50
    assert metrics["llm"]["total_completion_tokens"] >= 20
    assert metrics["llm"]["total_cost_usd"] >= 0.001
