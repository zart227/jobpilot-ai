"""Telegram review queue: status breakdown and stale card cleanup."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select, update

from app.db.models import Job, NotificationOutbox, Proposal, ProposalApprovalSnapshot, TelegramPending
from app.db.status_transitions import allows_proposal_approval_transition, allows_telegram_pending_transition
from app.db.workflow_states import (
    JOB_STATUS_PENDING_APPROVAL,
    JOB_STATUS_PERMANENT_ERROR,
    JOB_STATUS_SCORED,
    JOB_STATUS_SENT,
    JOB_STATUS_SIMULATED_SENT,
    JOB_STATUS_FILTERED,
    PROPOSAL_STATUS_PENDING_APPROVAL,
    PROPOSAL_STATUS_SKIPPED,
    TELEGRAM_PENDING_STATUS_SKIPPED,
)

logger = structlog.get_logger(__name__)

# Job left the human review queue (or never reached it) — Telegram card must not stay "pending".
_JOB_STATUSES_CLOSE_TELEGRAM_PENDING: frozenset[str] = frozenset(
    {
        JOB_STATUS_FILTERED,
        JOB_STATUS_SCORED,
        JOB_STATUS_PERMANENT_ERROR,
        JOB_STATUS_SENT,
        JOB_STATUS_SIMULATED_SENT,
    }
)


async def cancel_notification_outbox_for_telegram_pending(
    session, pending_id: uuid.UUID
) -> None:
    """Stop (re)delivery for a telegram_pending-linked outbox row."""
    await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.reference_type == "telegram_pending",
            NotificationOutbox.reference_id == pending_id,
            NotificationOutbox.status.in_(("pending", "needs_attention", "sending")),
        )
        .values(
            status="cancelled",
            claim_owner=None,
            claim_token=None,
            claim_expires_at=None,
            next_attempt_at=None,
        )
    )


async def close_telegram_pending_if_job_not_in_review(
    session, job: Job, *, reason: str = "job_no_longer_in_review"
) -> int:
    """Skip pending Telegram cards when the job is filtered/scored/etc.

    Returns the number of telegram_pending rows closed.
    """
    if job.status not in _JOB_STATUSES_CLOSE_TELEGRAM_PENDING:
        return 0

    result = await session.execute(
        select(TelegramPending).where(
            TelegramPending.job_id == job.id,
            TelegramPending.status == "pending",
        )
    )
    rows = result.scalars().all()
    closed = 0
    for tp in rows:
        prev = tp.status
        if allows_telegram_pending_transition(prev, TELEGRAM_PENDING_STATUS_SKIPPED):
            tp.status = TELEGRAM_PENDING_STATUS_SKIPPED
        else:
            logger.warning(
                "Cannot skip telegram_pending (transition denied)",
                pending_id=str(tp.id),
                job_id=str(job.id),
                previous=prev,
            )
            continue

        proposal = await session.get(Proposal, tp.proposal_id)
        if proposal and proposal.status == PROPOSAL_STATUS_PENDING_APPROVAL:
            if allows_proposal_approval_transition(proposal.status, PROPOSAL_STATUS_SKIPPED):
                proposal.status = PROPOSAL_STATUS_SKIPPED
            else:
                logger.warning(
                    "Cannot skip proposal with closed telegram_pending",
                    proposal_id=str(proposal.id),
                    job_id=str(job.id),
                )

        await cancel_notification_outbox_for_telegram_pending(session, tp.id)
        closed += 1

    if closed:
        logger.info(
            "Closed stale Telegram review cards",
            job_id=str(job.id),
            job_status=job.status,
            closed=closed,
            reason=reason,
        )
    return closed


async def telegram_pending_status_breakdown(
    session,
) -> tuple[int, int, int, int]:
    """Counts for /status: (total_pending, review_ready, assessment_stale, other_blocked).

    ``review_ready`` matches ops_pilot_status: Kwork gate passes (or non-kwork), snapshot exists,
    Telegram message was delivered (message_id set), job still ``pending_approval``.
    """
    from app.services.opportunity_policy import kwork_job_assessment_rejection

    pending_rows = (
        await session.scalars(select(TelegramPending).where(TelegramPending.status == "pending"))
    ).all()
    total = len(pending_rows)
    review_ready = 0
    assessment_stale = 0

    for tp in pending_rows:
        job = await session.get(Job, tp.job_id)
        if not job or job.status != JOB_STATUS_PENDING_APPROVAL:
            continue
        if job.platform == "kwork":
            gate = await kwork_job_assessment_rejection(session, job)
            if gate == "assessment_stale":
                assessment_stale += 1
                continue
            if gate is not None:
                continue
        snap = await session.get(ProposalApprovalSnapshot, tp.id) is not None
        if snap and tp.message_id is not None:
            review_ready += 1

    other = max(0, total - review_ready - assessment_stale)
    return total, review_ready, assessment_stale, other
