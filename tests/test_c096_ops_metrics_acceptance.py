"""C096: /stats ops metrics reflect queue age, errors, last success, unknown_result."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
    pytest.skip("integration DB required", allow_module_level=True)

from app.db.models import Job, NotificationOutbox, SendAttempt
from app.db.send_attempt_states import (
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KNOWN_FAILED,
    ATTEMPT_UNKNOWN_RESULT,
)
from app.db.session import AsyncSessionLocal
from app.db.workflow_states import JOB_STATUS_PERMANENT_ERROR, JOB_STATUS_RETRYABLE_ERROR
from app.services.ops_metrics import collect_ops_metrics


@pytest.mark.asyncio
async def test_ops_metrics_queue_age_errors_and_last_success() -> None:
    job_id = uuid.uuid4()
    send_finished = datetime.now(UTC) - timedelta(minutes=5)
    outbox_sent = datetime.now(UTC) - timedelta(minutes=2)
    retry_updated = datetime.now(UTC) - timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        before = await collect_ops_metrics(session)

        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c096-{job_id.int}",
                title="metrics",
                description="test",
                status=JOB_STATUS_RETRYABLE_ERROR,
                updated_at=retry_updated,
            )
        )
        session.add(
            Job(
                id=uuid.uuid4(),
                platform="kwork",
                external_id=f"c096-dead-{uuid.uuid4().int}",
                title="dead",
                description="test",
                status=JOB_STATUS_PERMANENT_ERROR,
            )
        )
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c096-unknown-{uuid.uuid4()}",
                job_id=job_id,
                snapshot_content="x",
                status=ATTEMPT_UNKNOWN_RESULT,
            )
        )
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c096-failed-{uuid.uuid4()}",
                job_id=job_id,
                snapshot_content="x",
                status=ATTEMPT_KNOWN_FAILED,
            )
        )
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c096-ok-{uuid.uuid4()}",
                job_id=job_id,
                snapshot_content="x",
                status=ATTEMPT_CONFIRMED_SENT,
                platform_response={"dry_run": False},
                finished_at=send_finished,
            )
        )
        session.add(
            NotificationOutbox(
                id=uuid.uuid4(),
                kind="proposal_approval",
                idempotency_key=f"c096-outbox-{uuid.uuid4()}",
                chat_id=1,
                payload={},
                reference_type="telegram_pending",
                reference_id=uuid.uuid4(),
                status="sent",
                sent_at=outbox_sent,
            )
        )
        session.add(
            NotificationOutbox(
                id=uuid.uuid4(),
                kind="proposal_approval",
                idempotency_key=f"c096-pending-{uuid.uuid4()}",
                chat_id=1,
                payload={},
                reference_type="telegram_pending",
                reference_id=uuid.uuid4(),
                status="pending",
                created_at=datetime.now(UTC) - timedelta(minutes=30),
            )
        )
        await session.commit()

        after = await collect_ops_metrics(session)

    assert after["send_attempts"]["unknown_result"] >= before["send_attempts"]["unknown_result"] + 1
    assert after["send_attempts"]["known_failed"] >= before["send_attempts"]["known_failed"] + 1
    assert after["jobs"]["permanent_error"] >= before["jobs"]["permanent_error"] + 1
    assert after["jobs"]["retryable_error"] >= before["jobs"]["retryable_error"] + 1
    assert after["jobs"]["oldest_retryable_age_seconds"] is not None
    assert after["jobs"]["oldest_retryable_age_seconds"] >= 3500
    assert after["notification_outbox"]["oldest_pending_age_seconds"] is not None
    assert after["notification_outbox"]["oldest_pending_age_seconds"] >= 1700
    assert after["last_success"]["confirmed_external_send_at"] is not None
    assert after["last_success"]["notification_outbox_sent_at"] is not None
