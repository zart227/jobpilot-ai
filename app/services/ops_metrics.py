"""Operational counters for queue health and delivery uncertainty."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboundMessage, Job, MemoryIndexPending, NotificationOutbox, SendAttempt
from app.db.send_attempt_states import (
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KIND_PROPOSAL,
    ATTEMPT_UNKNOWN_RESULT,
)


async def collect_ops_metrics(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(UTC)

    outbox_pending = await session.execute(
        select(func.count())
        .select_from(NotificationOutbox)
        .where(NotificationOutbox.status.in_(("pending", "sending")))
    )
    outbox_needs_attention = await session.execute(
        select(func.count())
        .select_from(NotificationOutbox)
        .where(NotificationOutbox.status == "needs_attention")
    )
    oldest_outbox = await session.execute(
        select(func.min(NotificationOutbox.created_at)).where(
            NotificationOutbox.status.in_(("pending", "sending"))
        )
    )
    oldest_outbox_at = oldest_outbox.scalar()
    outbox_age_seconds = None
    if oldest_outbox_at:
        outbox_age_seconds = int((now - oldest_outbox_at).total_seconds())

    unknown_attempts = await session.execute(
        select(func.count())
        .select_from(SendAttempt)
        .where(SendAttempt.status == ATTEMPT_UNKNOWN_RESULT)
    )
    sending_attempts = await session.execute(
        select(func.count()).select_from(SendAttempt).where(SendAttempt.status == "sending")
    )

    processing_jobs = await session.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.status == "processing")
    )
    retryable_jobs = await session.execute(
        select(func.count()).select_from(Job).where(Job.status == "retryable_error")
    )
    total_jobs = await session.execute(select(func.count()).select_from(Job))
    embedding_pending = await session.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.raw_data["embedding_pending"].as_boolean().is_(True))
    )
    memory_index_pending = await session.execute(
        select(func.count()).select_from(MemoryIndexPending)
    )
    oldest_memory_index = await session.execute(
        select(func.min(MemoryIndexPending.created_at))
    )
    oldest_memory_index_at = oldest_memory_index.scalar()
    memory_index_age_seconds = None
    if oldest_memory_index_at:
        memory_index_age_seconds = int((now - oldest_memory_index_at).total_seconds())
    inbound_needs_attention = await session.execute(
        select(func.count())
        .select_from(InboundMessage)
        .where(InboundMessage.status == "needs_attention")
    )
    inbound_received = await session.execute(
        select(func.count())
        .select_from(InboundMessage)
        .where(InboundMessage.status == "received")
    )
    llm_calls = await session.execute(
        select(
            func.coalesce(
                func.sum(cast(Job.raw_data["llm_stage_count"].astext, Integer)),
                0,
            )
        )
    )
    confirmed_send_jobs = await session.execute(
        select(func.count(func.distinct(SendAttempt.job_id))).where(
            SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            SendAttempt.kind == ATTEMPT_KIND_PROPOSAL,
            SendAttempt.job_id.is_not(None),
            # Legacy rows without an explicit execution mode cannot prove a live send.
            SendAttempt.platform_response.contains({"dry_run": False}),
            ~SendAttempt.platform_response.contains({"simulated": True}),
        )
    )

    total_jobs_count = int(total_jobs.scalar() or 0)
    confirmed_send_jobs_count = int(confirmed_send_jobs.scalar() or 0)

    return {
        "collected_at": now.isoformat(),
        "notification_outbox": {
            "pending_or_claimed": int(outbox_pending.scalar() or 0),
            "needs_attention": int(outbox_needs_attention.scalar() or 0),
            "oldest_pending_age_seconds": outbox_age_seconds,
        },
        "send_attempts": {
            "unknown_result": int(unknown_attempts.scalar() or 0),
            "sending": int(sending_attempts.scalar() or 0),
        },
        "jobs": {
            "total": total_jobs_count,
            "processing": int(processing_jobs.scalar() or 0),
            "retryable_error": int(retryable_jobs.scalar() or 0),
            "with_confirmed_send": confirmed_send_jobs_count,
        },
        "funnel": {
            "unique_jobs": total_jobs_count,
            "jobs_with_confirmed_external_send": confirmed_send_jobs_count,
        },
        "llm": {
            "pipeline_calls_recorded": int(llm_calls.scalar() or 0),
            "jobs_embedding_pending": int(embedding_pending.scalar() or 0),
            "memory_index_pending": int(memory_index_pending.scalar() or 0),
            "oldest_memory_index_pending_age_seconds": memory_index_age_seconds,
        },
        "inbound_messages": {
            "received": int(inbound_received.scalar() or 0),
            "needs_attention": int(inbound_needs_attention.scalar() or 0),
        },
    }
