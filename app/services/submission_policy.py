"""Rules for new Kwork offers vs editing an existing submitted offer (C051)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import Job, Proposal, SendAttempt
from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT, ATTEMPT_KIND_PROPOSAL
from app.db.workflow_states import (
    JOB_STATUS_SENT,
    JOB_SUBMITTED_STATUSES,
    PROPOSAL_STATUS_SENT,
    PROPOSAL_SUBMITTED_STATUSES,
)

SUBMISSION_NEW_OFFER = "new_offer"
SUBMISSION_EDIT_EXISTING = "edit_existing_offer"
VALID_SUBMISSION_ACTIONS = frozenset({SUBMISSION_NEW_OFFER, SUBMISSION_EDIT_EXISTING})


async def job_has_submitted_proposal(session, job_id: uuid.UUID) -> bool:
    job = await session.get(Job, job_id)
    if job and job.status in JOB_SUBMITTED_STATUSES:
        return True
    proposal_sent = await session.scalar(
        select(Proposal.id)
        .where(
            Proposal.job_id == job_id,
            Proposal.status.in_(tuple(PROPOSAL_SUBMITTED_STATUSES)),
        )
        .limit(1)
    )
    if proposal_sent is not None:
        return True
    attempt = await session.scalar(
        select(SendAttempt.id)
        .where(
            SendAttempt.kind == ATTEMPT_KIND_PROPOSAL,
            SendAttempt.job_id == job_id,
            SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
        )
        .limit(1)
    )
    return attempt is not None


async def job_has_live_submitted_proposal(session, job_id: uuid.UUID) -> bool:
    """True only when a live (non-dry-run) proposal delivery was confirmed."""
    from app.services.delivery_scope import is_live_confirmed_send_attempt

    job = await session.get(Job, job_id)
    if job and job.status == JOB_STATUS_SENT:
        return True
    proposal_live = await session.scalar(
        select(Proposal.id)
        .where(
            Proposal.job_id == job_id,
            Proposal.status == PROPOSAL_STATUS_SENT,
        )
        .limit(1)
    )
    if proposal_live is not None:
        return True
    attempts = (
        await session.scalars(
            select(SendAttempt)
            .where(
                SendAttempt.kind == ATTEMPT_KIND_PROPOSAL,
                SendAttempt.job_id == job_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
            .limit(5)
        )
    ).all()
    return any(is_live_confirmed_send_attempt(row) for row in attempts)


async def resolve_submission_action(session, job_id: uuid.UUID) -> str:
    if await job_has_submitted_proposal(session, job_id):
        return SUBMISSION_EDIT_EXISTING
    return SUBMISSION_NEW_OFFER


def validate_submission_action_for_send(
    action: str | None,
    *,
    job_has_submitted: bool,
) -> str | None:
    """Return block reason or None if allowed."""
    normalized = action or SUBMISSION_NEW_OFFER
    if normalized not in VALID_SUBMISSION_ACTIONS:
        return "invalid_submission_action"
    if normalized == SUBMISSION_NEW_OFFER and job_has_submitted:
        return "new_offer_after_sent"
    if normalized == SUBMISSION_EDIT_EXISTING and not job_has_submitted:
        return "edit_without_prior_send"
    return None


async def pending_snapshot_is_edit_existing(session, pending_id: uuid.UUID) -> bool:
    from app.db.models import ProposalApprovalSnapshot

    snapshot = await session.get(ProposalApprovalSnapshot, pending_id)
    return bool(
        snapshot and snapshot.payload.get("submission_action") == SUBMISSION_EDIT_EXISTING
    )

