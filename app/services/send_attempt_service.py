"""Atomic claims and outcomes for external proposal/reply delivery."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db.models import Interaction, Job, Proposal, SendAttempt, TelegramInboxPending
from app.db.send_attempt_states import (
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KIND_INBOX_REPLY,
    ATTEMPT_KIND_PROPOSAL,
    ATTEMPT_KNOWN_FAILED,
    ATTEMPT_NEEDS_ATTENTION,
    ATTEMPT_PENDING,
    ATTEMPT_SENDING,
    ATTEMPT_UNKNOWN_RESULT,
)
from app.db.session import AsyncSessionLocal
from app.db.status_transitions import allows_send_attempt_transition
from app.db.workflow_states import (
    JOB_STATUS_SENT,
    JOB_STATUS_SIMULATED_SENT,
    PROPOSAL_STATUS_SENT,
    PROPOSAL_STATUS_SIMULATED_SENT,
    proposal_is_submitted,
)
from app.services.approval_snapshot import matching_proposal_approval
from app.services.delivery_scope import is_live_confirmed_send_attempt
from app.services.inbox_approval import matching_inbox_approval
from app.services.notification_outbox import compute_backoff_seconds
from app.services.submission_policy import (
    SUBMISSION_EDIT_EXISTING,
    SUBMISSION_NEW_OFFER,
    job_has_live_submitted_proposal,
    job_has_submitted_proposal,
    validate_submission_action_for_send,
)
from app.utils.delivery_errors import is_ambiguous_send_error

logger = structlog.get_logger(__name__)

ClaimAction = Literal["proceed", "already_sent", "blocked", "unchanged"]


@dataclass(frozen=True)
class ClaimResult:
    action: ClaimAction
    attempt_id: uuid.UUID | None = None
    claim_token: uuid.UUID | None = None
    reason: str | None = None
    approved_payload: dict[str, Any] | None = None


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def proposal_idempotency_key(
    proposal_id: uuid.UUID,
    version: int,
    content: str,
    *,
    submission_action: str = SUBMISSION_NEW_OFFER,
    simulated: bool = False,
) -> str:
    prefix = "proposal_edit" if submission_action == SUBMISSION_EDIT_EXISTING else "proposal"
    mode = "sim" if simulated else "live"
    return f"{prefix}:{proposal_id}:v{version}:{mode}:{content_fingerprint(content)}"


def inbox_reply_idempotency_key(pending_id: uuid.UUID, content: str) -> str:
    return f"inbox_reply:{pending_id}:{content_fingerprint(content)}"


async def delivery_blocks_mutation(
    session,
    *,
    job_id: uuid.UUID | None = None,
    inbox_pending_id: uuid.UUID | None = None,
    include_confirmed: bool = False,
) -> bool:
    """Caller holds the same business lock as claim/finalize before mutating a draft.

    Lease expiry does not make an uncertain external delivery safe to overwrite.
    """
    if (job_id is None) == (inbox_pending_id is None):
        raise ValueError("Specify exactly one delivery target")
    states = [ATTEMPT_SENDING, ATTEMPT_UNKNOWN_RESULT, ATTEMPT_NEEDS_ATTENTION]
    if include_confirmed:
        states.append(ATTEMPT_CONFIRMED_SENT)
    target = (
        (SendAttempt.kind == ATTEMPT_KIND_PROPOSAL) & (SendAttempt.job_id == job_id)
        if job_id is not None
        else (SendAttempt.kind == ATTEMPT_KIND_INBOX_REPLY)
        & (SendAttempt.inbox_pending_id == inbox_pending_id)
    )
    return (
        await session.scalar(
            select(SendAttempt.id).where(target, SendAttempt.status.in_(states)).limit(1)
        )
        is not None
    )


async def _expire_stale_sending_attempt(attempt: SendAttempt, settings: Settings) -> None:
    now = datetime.now(UTC)
    if attempt.status != ATTEMPT_SENDING:
        return
    if attempt.claim_expires_at and attempt.claim_expires_at >= now:
        return
    _set_attempt_status(attempt, ATTEMPT_UNKNOWN_RESULT)
    attempt.last_error = "lease_expired_without_confirmation"
    attempt.claim_owner = None
    attempt.claim_token = None
    attempt.claim_expires_at = None
    attempt.finished_at = now


def _set_attempt_status(attempt: SendAttempt, new_status: str) -> bool:
    if not allows_send_attempt_transition(attempt.status, new_status):
        logger.warning(
            "Rejected send attempt status transition",
            attempt_id=str(attempt.id),
            previous=attempt.status,
            new_status=new_status,
        )
        return False
    attempt.status = new_status
    return True


async def _locked_row(session, model, row_id):
    result = await session.execute(
        select(model)
        .where(model.id == row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _lock_delivery_target(session, kind, job_id, proposal_id, inbox_pending_id):
    # Shared lock order everywhere: business object, then attempt. No network in TX.
    if kind == ATTEMPT_KIND_PROPOSAL and job_id and proposal_id:
        job = await _locked_row(session, Job, job_id)
        proposal = await _locked_row(session, Proposal, proposal_id)
        return bool(job and proposal and proposal.job_id == job.id)
    if kind == ATTEMPT_KIND_INBOX_REPLY and inbox_pending_id:
        return await _locked_row(session, TelegramInboxPending, inbox_pending_id) is not None
    return False


async def _lock_attempt(session, attempt_id):
    attempt = await session.get(SendAttempt, attempt_id)
    if not attempt:
        return None
    if not await _lock_delivery_target(
        session,
        attempt.kind,
        attempt.job_id,
        attempt.proposal_id,
        attempt.inbox_pending_id,
    ):
        return None
    return await _locked_row(session, SendAttempt, attempt_id)


async def _claim_locked(
    session,
    *,
    kind,
    idempotency_key,
    snapshot_content,
    snapshot_version,
    snapshot_metadata,
    job_id=None,
    proposal_id=None,
    inbox_pending_id=None,
    claim_owner="send_worker",
    settings=None,
    allow_resubmit=False,
):
    settings = settings or get_settings()
    now = datetime.now(UTC)
    scope = (
        SendAttempt.job_id == job_id
        if kind == ATTEMPT_KIND_PROPOSAL
        else SendAttempt.inbox_pending_id == inbox_pending_id
    )
    result = await session.execute(
        select(SendAttempt)
        .where(SendAttempt.kind == kind, scope)
        .order_by(SendAttempt.created_at)
        .with_for_update()
    )
    attempts = list(result.scalars())
    attempt = next((row for row in attempts if row.idempotency_key == idempotency_key), None)
    for row in attempts:
        await _expire_stale_sending_attempt(row, settings)
        if row.status in (ATTEMPT_SENDING, ATTEMPT_UNKNOWN_RESULT, ATTEMPT_NEEDS_ATTENTION):
            await session.commit()
            return ClaimResult(
                action="blocked",
                attempt_id=row.id,
                reason="already_sending" if row.status == ATTEMPT_SENDING else row.status,
            )
    if attempt and attempt.status == ATTEMPT_CONFIRMED_SENT:
        return ClaimResult(action="already_sent", attempt_id=attempt.id)
    if (
        not allow_resubmit
        and kind == ATTEMPT_KIND_PROPOSAL
        and any(
            row.status == ATTEMPT_CONFIRMED_SENT and is_live_confirmed_send_attempt(row)
            for row in attempts
        )
    ):
        return ClaimResult(action="blocked", reason="job_already_sent")
    if attempt:
        identity_mismatch = (
            attempt.snapshot_content != snapshot_content
            or attempt.snapshot_version != snapshot_version
            or attempt.proposal_id != proposal_id
            or attempt.inbox_pending_id != inbox_pending_id
        )
        metadata_mismatch = (attempt.snapshot_metadata or {}) != snapshot_metadata
        if identity_mismatch or metadata_mismatch:
            if attempt.status in (ATTEMPT_PENDING, ATTEMPT_KNOWN_FAILED) and not identity_mismatch:
                attempt.snapshot_metadata = snapshot_metadata
            else:
                return ClaimResult(
                    action="blocked", attempt_id=attempt.id, reason="snapshot_mismatch"
                )
        if attempt.next_attempt_at and attempt.next_attempt_at > now:
            return ClaimResult(action="blocked", attempt_id=attempt.id, reason="backoff")
        if (attempt.attempt_count or 0) >= settings.send_attempt_max_attempts:
            _set_attempt_status(attempt, ATTEMPT_NEEDS_ATTENTION)
            await session.commit()
            return ClaimResult(action="blocked", attempt_id=attempt.id, reason="needs_attention")
        if attempt.status not in (ATTEMPT_PENDING, ATTEMPT_KNOWN_FAILED):
            return ClaimResult(
                action="blocked", attempt_id=attempt.id, reason="invalid_attempt_state"
            )
    else:
        attempt = SendAttempt(
            kind=kind,
            idempotency_key=idempotency_key,
            job_id=job_id,
            proposal_id=proposal_id,
            inbox_pending_id=inbox_pending_id,
            snapshot_content=snapshot_content,
            snapshot_version=snapshot_version,
            snapshot_metadata=snapshot_metadata,
            status=ATTEMPT_PENDING,
        )
        session.add(attempt)
        await session.flush()
    token = uuid.uuid4()
    if not _set_attempt_status(attempt, ATTEMPT_SENDING):
        await session.rollback()
        return ClaimResult(action="blocked", attempt_id=attempt.id, reason="invalid_attempt_state")
    attempt.claim_owner = claim_owner
    attempt.claim_token = token
    attempt.claim_expires_at = now + timedelta(seconds=settings.send_attempt_lease_seconds)
    attempt.started_at = now
    attempt.next_attempt_at = None
    attempt.attempt_count = (attempt.attempt_count or 0) + 1
    await session.commit()
    return ClaimResult(action="proceed", attempt_id=attempt.id, claim_token=token)


async def claim_send_attempt(
    *,
    kind: str,
    idempotency_key: str,
    snapshot_content: str,
    snapshot_version: int | None,
    snapshot_metadata: dict[str, Any],
    job_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    inbox_pending_id: uuid.UUID | None = None,
    claim_owner: str = "send_worker",
    settings: Settings | None = None,
    allow_resubmit: bool = False,
) -> ClaimResult:
    from sqlalchemy.exc import IntegrityError

    async with AsyncSessionLocal() as session:
        if not await _lock_delivery_target(session, kind, job_id, proposal_id, inbox_pending_id):
            return ClaimResult(action="blocked", reason="invalid_delivery_target")
        try:
            return await _claim_locked(
                session,
                kind=kind,
                idempotency_key=idempotency_key,
                snapshot_content=snapshot_content,
                snapshot_version=snapshot_version,
                snapshot_metadata=snapshot_metadata,
                job_id=job_id,
                proposal_id=proposal_id,
                inbox_pending_id=inbox_pending_id,
                claim_owner=claim_owner,
                settings=settings,
                allow_resubmit=allow_resubmit,
            )
        except IntegrityError:
            await session.rollback()
            return ClaimResult(action="blocked", reason="idempotency_conflict")


async def mark_unknown_after_commit_failure(
    attempt_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    error: str,
    platform_response: dict[str, Any] | None = None,
) -> None:
    """Platform may have succeeded while DB commit failed — never blind retry."""
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        attempt = await _lock_attempt(session, attempt_id)
        if not attempt:
            return
        if attempt.status != ATTEMPT_SENDING or attempt.claim_token != claim_token:
            return
        _set_attempt_status(attempt, ATTEMPT_UNKNOWN_RESULT)
        attempt.last_error = error[:2000]
        attempt.claim_token = None
        attempt.claim_owner = None
        attempt.claim_expires_at = None
        attempt.finished_at = now
        if platform_response:
            merged = dict(attempt.platform_response or {})
            merged.update(platform_response)
            attempt.platform_response = merged
        await session.commit()


async def finalize_send_attempt(
    attempt_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    success: bool,
    error: str | None = None,
    platform_response: dict[str, Any] | None = None,
    ambiguous: bool = False,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    ambiguous = ambiguous or is_ambiguous_send_error(error)

    async with AsyncSessionLocal() as session:
        attempt = await _lock_attempt(session, attempt_id)
        if not attempt:
            return False
        if attempt.status != ATTEMPT_SENDING or attempt.claim_token != claim_token:
            logger.warning(
                "Send attempt finalize rejected: claim token mismatch",
                attempt_id=str(attempt_id),
            )
            return False

        attempt.finished_at = now
        attempt.claim_owner = None
        attempt.claim_expires_at = None
        attempt.last_error = (error or "")[:2000] if error else None
        if platform_response:
            attempt.platform_response = platform_response

        try:
            if success:
                if not _set_attempt_status(attempt, ATTEMPT_CONFIRMED_SENT):
                    await session.rollback()
                    return False
                attempt.claim_token = None
                await _apply_confirmed_send(session, attempt)
            elif ambiguous:
                if not _set_attempt_status(attempt, ATTEMPT_UNKNOWN_RESULT):
                    await session.rollback()
                    return False
                attempt.claim_token = None
            elif attempt.attempt_count >= settings.send_attempt_max_attempts:
                if not _set_attempt_status(attempt, ATTEMPT_NEEDS_ATTENTION):
                    await session.rollback()
                    return False
                attempt.claim_token = None
            else:
                if not _set_attempt_status(attempt, ATTEMPT_KNOWN_FAILED):
                    await session.rollback()
                    return False
                attempt.claim_token = None
                delay = compute_backoff_seconds(
                    attempt.attempt_count,
                    settings.send_attempt_base_backoff_seconds,
                )
                attempt.next_attempt_at = now + timedelta(seconds=delay)
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.error(
                "Send attempt finalize commit failed",
                attempt_id=str(attempt_id),
                success=success,
                error=str(exc),
            )
            if success:
                await mark_unknown_after_commit_failure(
                    attempt_id,
                    claim_token,
                    error=f"commit_failed_after_platform_success: {exc}",
                    platform_response=platform_response,
                )
            return False
        return True


async def _ensure_inbox_outbound_interaction(
    session, attempt: SendAttempt, pending: TelegramInboxPending | None
) -> None:
    """Persist the sent snapshot in the same transaction, preserving legacy history."""
    existing = await session.scalar(
        select(Interaction.id).where(
            Interaction.direction == "outbound",
            Interaction.metadata_["send_attempt_id"].astext == str(attempt.id),
        ).limit(1)
    )
    if existing is not None:
        return
    meta = attempt.snapshot_metadata or {}
    approved = meta.get("approved_payload") or {}
    interaction_id = approved.get("interaction_id")
    if not interaction_id and pending and pending.interaction_id:
        interaction_id = str(pending.interaction_id)
    session.add(Interaction(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"jobpilot:inbox-outbound:{attempt.id}"),
        job_id=attempt.job_id,
        proposal_id=attempt.proposal_id,
        direction="outbound",
        channel="kwork",
        message=attempt.snapshot_content,
        intent="reply_sent",
        metadata_={
            "username": meta.get("username"),
            "user_id": meta.get("user_id"),
            "pending_id": str(attempt.inbox_pending_id),
            "interaction_id": interaction_id,
            "send_attempt_id": str(attempt.id),
            "dry_run": bool((attempt.platform_response or {}).get("dry_run")),
        },
    ))


async def _apply_confirmed_send(session, attempt: SendAttempt) -> None:
    from app.services.delivery_scope import platform_response_is_simulation

    simulated = platform_response_is_simulation(attempt.platform_response)
    proposal_status = PROPOSAL_STATUS_SIMULATED_SENT if simulated else PROPOSAL_STATUS_SENT
    job_status = JOB_STATUS_SIMULATED_SENT if simulated else JOB_STATUS_SENT
    pending_status = "simulated_sent" if simulated else "sent"

    if attempt.kind == ATTEMPT_KIND_PROPOSAL and attempt.proposal_id:
        proposal = await session.get(Proposal, attempt.proposal_id)
        if proposal:
            if (
                attempt.snapshot_version is not None
                and proposal.version != attempt.snapshot_version
            ) or proposal.content != attempt.snapshot_content:
                logger.warning(
                    "Proposal version changed after send snapshot",
                    proposal_id=str(proposal.id),
                    snapshot_version=attempt.snapshot_version,
                    current_version=proposal.version,
                )
            else:
                proposal.status = proposal_status
                proposal.sent_at = datetime.now(UTC)
        if attempt.job_id:
            job = await session.get(Job, attempt.job_id)
            if job:
                job.status = job_status
    elif attempt.kind == ATTEMPT_KIND_INBOX_REPLY and attempt.inbox_pending_id:
        pending = await session.get(TelegramInboxPending, attempt.inbox_pending_id)
        await _ensure_inbox_outbound_interaction(session, attempt, pending)
        if pending and pending.draft_reply == attempt.snapshot_content:
            pending.status = pending_status
        elif pending:
            logger.warning("Inbox draft changed after send snapshot", pending_id=str(pending.id))


async def prepare_proposal_send(
    job_id: str,
    proposal_id: str,
    content: str,
    *,
    allow_resubmit: bool | None = None,
    claim_owner: str = "proposal_sender",
) -> tuple[ClaimResult, Job | None, Proposal | None]:
    async with AsyncSessionLocal() as session:
        job = await _locked_row(session, Job, uuid.UUID(job_id))
        proposal = await _locked_row(session, Proposal, uuid.UUID(proposal_id))
        if not job or not proposal or proposal.job_id != job.id:
            return ClaimResult(action="blocked", reason="invalid_delivery_target"), job, proposal
        if proposal.content != content:
            return ClaimResult(action="blocked", reason="content_not_approved"), job, proposal
        approval = await matching_proposal_approval(session, job, proposal)
        if approval is None:
            from app.db.models import ProposalApprovalEvent
            from app.services.approval_snapshot import proposal_card_payload

            stale_new = await session.scalar(
                select(ProposalApprovalEvent)
                .where(
                    ProposalApprovalEvent.proposal_id == proposal.id,
                    ProposalApprovalEvent.payload
                    == proposal_card_payload(job, proposal, submission_action=SUBMISSION_NEW_OFFER),
                )
                .limit(1)
            )
            if stale_new and await job_has_submitted_proposal(session, job.id):
                return ClaimResult(action="blocked", reason="new_offer_after_sent"), job, proposal
            return ClaimResult(action="blocked", reason="durable_approval_required"), job, proposal
        submission_action = approval.payload.get("submission_action", SUBMISSION_NEW_OFFER)
        has_submitted = await job_has_live_submitted_proposal(session, job.id)
        policy_error = validate_submission_action_for_send(
            submission_action,
            job_has_submitted=has_submitted,
        )
        if policy_error:
            return ClaimResult(action="blocked", reason=policy_error), job, proposal
        allow_resubmit = submission_action == SUBMISSION_EDIT_EXISTING
        if proposal_is_submitted(proposal.status) and not allow_resubmit:
            if (
                proposal.status == PROPOSAL_STATUS_SIMULATED_SENT
                and not await job_has_live_submitted_proposal(session, job.id)
            ):
                pass
            else:
                return ClaimResult(action="unchanged"), job, proposal
        allowed_statuses = ("approved", "edited", PROPOSAL_STATUS_SIMULATED_SENT)
        if proposal.status not in allowed_statuses:
            return ClaimResult(action="blocked", reason="proposal_not_approved"), job, proposal
        from app.services.opportunity_policy import kwork_job_assessment_rejection

        policy_error = await kwork_job_assessment_rejection(session, job)
        if policy_error:
            return ClaimResult(action="blocked", reason=policy_error), job, proposal
        metadata = {
            "approval_event_id": str(approval.id),
            "approved_payload": approval.payload,
            "submission_action": submission_action,
            "platform": job.platform,
            "url": job.url,
            "budget_min": float(job.budget_min) if job.budget_min else None,
            "budget_max": float(job.budget_max) if job.budget_max else None,
        }
        settings = get_settings()
        claim = await _claim_locked(
            session,
            kind=ATTEMPT_KIND_PROPOSAL,
            idempotency_key=proposal_idempotency_key(
                proposal.id,
                proposal.version,
                content,
                submission_action=submission_action,
                simulated=settings.platform_dry_run,
            ),
            snapshot_content=content,
            snapshot_version=proposal.version,
            snapshot_metadata=metadata,
            job_id=job.id,
            proposal_id=proposal.id,
            claim_owner=claim_owner,
            allow_resubmit=allow_resubmit,
        )
        return replace(claim, approved_payload=approval.payload), job, proposal


async def prepare_inbox_reply_send(
    *,
    pending_id: uuid.UUID,
    reply_text: str,
    expected_user_id: int,
    expected_username: str | None = None,
    claim_owner: str = "inbox_reply",
) -> ClaimResult:
    async with AsyncSessionLocal() as session:
        pending = await _locked_row(session, TelegramInboxPending, pending_id)
        if not pending:
            return ClaimResult(action="blocked", reason="pending_not_found")
        if pending.kwork_user_id != expected_user_id or (
            expected_username is not None and pending.kwork_username != expected_username
        ):
            return ClaimResult(action="blocked", reason="user_mismatch")
        if pending.draft_reply != reply_text:
            return ClaimResult(action="blocked", reason="content_not_approved")
        if pending.status in ("sent", "simulated_sent"):
            return ClaimResult(action="already_sent", reason="already_sent")
        if pending.status not in ("approved", "edited"):
            return ClaimResult(action="blocked", reason="reply_not_approved")
        approval = await matching_inbox_approval(session, pending)
        if approval is None:
            return ClaimResult(action="blocked", reason="durable_approval_required")
        claim = await _claim_locked(
            session,
            kind=ATTEMPT_KIND_INBOX_REPLY,
            idempotency_key=inbox_reply_idempotency_key(pending_id, reply_text),
            snapshot_content=reply_text,
            snapshot_version=None,
            snapshot_metadata={
                "username": pending.kwork_username,
                "user_id": expected_user_id,
                "approval_event_id": str(approval.id),
                "approved_payload": approval.payload,
            },
            job_id=pending.job_id,
            proposal_id=pending.proposal_id,
            inbox_pending_id=pending.id,
            claim_owner=claim_owner,
        )

        return replace(claim, approved_payload=approval.payload)
