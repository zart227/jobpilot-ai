"""Operational counters for queue health and delivery uncertainty."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboundMessage, Job, MemoryIndexPending, NotificationOutbox, SendAttempt
from app.db.send_attempt_states import (
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KIND_PROPOSAL,
    ATTEMPT_KNOWN_FAILED,
    ATTEMPT_UNKNOWN_RESULT,
)
from app.db.workflow_states import JOB_STATUS_PERMANENT_ERROR


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
    known_failed_attempts = await session.execute(
        select(func.count())
        .select_from(SendAttempt)
        .where(SendAttempt.status == ATTEMPT_KNOWN_FAILED)
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
    permanent_error_jobs = await session.execute(
        select(func.count()).select_from(Job).where(Job.status == JOB_STATUS_PERMANENT_ERROR)
    )
    oldest_retryable = await session.execute(
        select(func.min(Job.updated_at)).where(Job.status == "retryable_error")
    )
    oldest_retryable_at = oldest_retryable.scalar()
    retryable_queue_age_seconds = None
    if oldest_retryable_at:
        retryable_queue_age_seconds = int((now - oldest_retryable_at).total_seconds())
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
    llm_cost = await session.execute(
        select(
            func.coalesce(
                func.sum(cast(Job.raw_data["llm_usage_summary"]["total_cost_usd"].astext, Numeric)),
                0,
            )
        )
    )
    llm_prompt_tokens = await session.execute(
        select(
            func.coalesce(
                func.sum(
                    cast(Job.raw_data["llm_usage_summary"]["total_prompt_tokens"].astext, Integer)
                ),
                0,
            )
        )
    )
    llm_completion_tokens = await session.execute(
        select(
            func.coalesce(
                func.sum(
                    cast(
                        Job.raw_data["llm_usage_summary"]["total_completion_tokens"].astext,
                        Integer,
                    )
                ),
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
    last_confirmed_send = await session.execute(
        select(func.max(SendAttempt.finished_at)).where(
            SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            SendAttempt.platform_response.contains({"dry_run": False}),
            ~SendAttempt.platform_response.contains({"simulated": True}),
        )
    )
    last_outbox_sent = await session.execute(
        select(func.max(NotificationOutbox.sent_at)).where(NotificationOutbox.status == "sent")
    )

    total_jobs_count = int(total_jobs.scalar() or 0)
    confirmed_send_jobs_count = int(confirmed_send_jobs.scalar() or 0)
    last_confirmed_at = last_confirmed_send.scalar()
    last_outbox_at = last_outbox_sent.scalar()

    return {
        "collected_at": now.isoformat(),
        "notification_outbox": {
            "pending_or_claimed": int(outbox_pending.scalar() or 0),
            "needs_attention": int(outbox_needs_attention.scalar() or 0),
            "oldest_pending_age_seconds": outbox_age_seconds,
        },
        "send_attempts": {
            "unknown_result": int(unknown_attempts.scalar() or 0),
            "known_failed": int(known_failed_attempts.scalar() or 0),
            "sending": int(sending_attempts.scalar() or 0),
        },
        "jobs": {
            "total": total_jobs_count,
            "processing": int(processing_jobs.scalar() or 0),
            "retryable_error": int(retryable_jobs.scalar() or 0),
            "permanent_error": int(permanent_error_jobs.scalar() or 0),
            "oldest_retryable_age_seconds": retryable_queue_age_seconds,
            "with_confirmed_send": confirmed_send_jobs_count,
        },
        "last_success": {
            "confirmed_external_send_at": (
                last_confirmed_at.isoformat() if last_confirmed_at else None
            ),
            "notification_outbox_sent_at": (
                last_outbox_at.isoformat() if last_outbox_at else None
            ),
        },
        "funnel": {
            "unique_jobs": total_jobs_count,
            "jobs_with_confirmed_external_send": confirmed_send_jobs_count,
        },
        "llm": {
            "pipeline_calls_recorded": int(llm_calls.scalar() or 0),
            "total_cost_usd": float(llm_cost.scalar() or 0),
            "total_prompt_tokens": int(llm_prompt_tokens.scalar() or 0),
            "total_completion_tokens": int(llm_completion_tokens.scalar() or 0),
            "jobs_embedding_pending": int(embedding_pending.scalar() or 0),
            "memory_index_pending": int(memory_index_pending.scalar() or 0),
            "oldest_memory_index_pending_age_seconds": memory_index_age_seconds,
        },
        "inbound_messages": {
            "received": int(inbound_received.scalar() or 0),
            "needs_attention": int(inbound_needs_attention.scalar() or 0),
        },
    }
