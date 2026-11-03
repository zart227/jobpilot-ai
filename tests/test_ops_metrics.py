"""Operational metrics must consume each SQLAlchemy result only once."""

from unittest.mock import AsyncMock

from sqlalchemy import create_engine, text

from app.services.ops_metrics import collect_ops_metrics


async def test_counts_can_be_reused_in_job_and_funnel_metrics() -> None:
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            values = [2, 1, None, 0, 1, 3, 4, 42, 5, 0, None, 0, 3, 7, 9]
            results = [connection.execute(text("SELECT :value"), {"value": v}) for v in values]
            session = AsyncMock()
            session.execute.side_effect = results
            metrics = await collect_ops_metrics(session)
        assert metrics["jobs"]["total"] == metrics["funnel"]["unique_jobs"] == 42
        assert metrics["jobs"]["with_confirmed_send"] == 9
        assert metrics["funnel"]["jobs_with_confirmed_external_send"] == 9
    finally:
        engine.dispose()

async def test_funnel_excludes_dry_run_inbox_and_unverified_legacy_modes():
    import os
    import uuid

    import pytest
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        pytest.skip("isolated DB required")
    from app.db.models import Job, NotificationOutbox, SendAttempt
    from app.db.session import AsyncSessionLocal

    ids = [uuid.uuid4() for _ in range(5)]
    async with AsyncSessionLocal() as session:
        before = await collect_ops_metrics(session)
        for job_id in ids:
            session.add(Job(id=job_id, platform="kwork", external_id=f"kwork-{job_id.int}",
                            title="test", description="test", status="new"))
        await session.flush()
        for index, (kind, result) in enumerate([
            ("proposal", {"dry_run": False}),
            ("proposal", {"dry_run": False}),
            ("proposal", {"dry_run": True}),
            ("inbox_reply", {"dry_run": False}),
            ("proposal", {}),
            ("proposal", {"dry_run": False, "simulated": True}),
        ]):
            job_id = ids[[0, 0, 1, 2, 3, 4][index]]
            session.add(SendAttempt(
                id=uuid.uuid4(), kind=kind, idempotency_key=f"metric-{uuid.uuid4()}",
                job_id=job_id, snapshot_content="test", status="confirmed_sent",
                platform_response=result,
            ))
        for status in ("pending", "sending", "sent"):
            session.add(NotificationOutbox(
                id=uuid.uuid4(), kind="proposal_approval", idempotency_key=f"metric-{uuid.uuid4()}",
                chat_id=123, payload={}, reference_type="telegram_pending", reference_id=uuid.uuid4(),
                status=status,
            ))
        await session.commit()
        after = await collect_ops_metrics(session)
        assert after["notification_outbox"]["pending_or_claimed"] == (
            before["notification_outbox"]["pending_or_claimed"] + 2
        )
        assert after["funnel"]["unique_jobs"] == before["funnel"]["unique_jobs"] + 5
        assert after["funnel"]["jobs_with_confirmed_external_send"] == (
            before["funnel"]["jobs_with_confirmed_external_send"] + 1
        )
        assert "inbound_messages" in after
        assert after["inbound_messages"]["received"] >= before["inbound_messages"]["received"]
