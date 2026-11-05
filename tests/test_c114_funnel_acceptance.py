"""C114: funnel counts unique jobs with verified live confirmed proposal sends."""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated *_test database required",
)


@pytest.mark.asyncio
async def test_duplicate_confirmed_proposal_sends_count_job_once() -> None:
    from app.db.models import Job, SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.ops_metrics import collect_ops_metrics

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        before = await collect_ops_metrics(session)
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c114-{job_id.int}",
                title="C114 funnel",
                description="C114",
                status="new",
            )
        )
        await session.flush()
        for _ in range(2):
            session.add(
                SendAttempt(
                    id=uuid.uuid4(),
                    kind="proposal",
                    idempotency_key=f"c114-{uuid.uuid4()}",
                    job_id=job_id,
                    snapshot_content="body",
                    status="confirmed_sent",
                    platform_response={"dry_run": False},
                )
            )
        await session.commit()
        after = await collect_ops_metrics(session)

    assert after["funnel"]["unique_jobs"] == before["funnel"]["unique_jobs"] + 1
    assert (
        after["funnel"]["jobs_with_confirmed_external_send"]
        == before["funnel"]["jobs_with_confirmed_external_send"] + 1
    )


@pytest.mark.asyncio
async def test_legacy_attempt_without_dry_run_flag_not_counted_in_funnel() -> None:
    from app.db.models import Job, SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.ops_metrics import collect_ops_metrics

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        before = await collect_ops_metrics(session)
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c114-legacy-{job_id.int}",
                title="legacy",
                description="legacy",
                status="new",
            )
        )
        await session.flush()
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c114-legacy-{uuid.uuid4()}",
                job_id=job_id,
                snapshot_content="body",
                status="confirmed_sent",
                platform_response={},
            )
        )
        await session.commit()
        after = await collect_ops_metrics(session)

    assert after["funnel"]["unique_jobs"] == before["funnel"]["unique_jobs"] + 1
    assert (
        after["funnel"]["jobs_with_confirmed_external_send"]
        == before["funnel"]["jobs_with_confirmed_external_send"]
    )
