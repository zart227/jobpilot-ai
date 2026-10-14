"""Atomic claims and outcomes for external proposal/reply delivery."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db.models import Job, Proposal, SendAttempt, TelegramInboxPending
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
from app.services.notification_outbox import compute_backoff_seconds
from app.utils.delivery_errors import is_ambiguous_send_error

logger = structlog.get_logger(__name__)

ClaimAction = Literal["proceed", "already_sent", "blocked", "unchanged"]


@dataclass(frozen=True)
class ClaimResult:
    action: ClaimAction
    attempt_id: uuid.UUID | None = None
    claim_token: uuid.UUID | None = None
    reason: str | None = None


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def proposal_idempotency_key(proposal_id: uuid.UUID, version: int, content: str) -> str:
    return f"proposal:{proposal_id}:v{version}:{content_fingerprint(content)}"


def inbox_reply_idempotency_key(pending_id: uuid.UUID, content: str) -> str:
    return f"inbox_reply:{pending_id}:{content_fingerprint(content)}"


async def _expire_stale_sending_attempt(attempt: SendAttempt, settings: Settings) -> None:
    now = datetime.now(UTC)
    if attempt.status != ATTEMPT_SENDING:
        return
    if attempt.claim_expires_at and attempt.claim_expires_at >= now:
        return
    attempt.status = ATTEMPT_UNKNOWN_RESULT
    attempt.last_error = "lease_expired_without_confirmation"
    attempt.claim_owner = None
    attempt.claim_token = None
    attempt.claim_expires_at = None
    attempt.finished_at = now


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
) -> ClaimResult:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=settings.send_attempt_lease_seconds)
    claim_token = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(SendAttempt).where(SendAttempt.idempotency_key == idempotency_key)
        )
        attempt = existing.scalar_one_or_none()

        if attempt is None:
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
        else:
            await _expire_stale_sending_attempt(attempt, settings)
            if attempt.status == ATTEMPT_CONFIRMED_SENT:
                await session.commit()
                return ClaimResult(action="already_sent", attempt_id=attempt.id)
            if attempt.status == ATTEMPT_UNKNOWN_RESULT:
                from app.services.send_attempt_reconcile import reconcile_send_attempt

                outcome = await reconcile_send_attempt(attempt.id, settings=settings)
                await session.refresh(attempt)
                if outcome == "confirmed" and attempt.status == ATTEMPT_CONFIRMED_SENT:
                    await session.commit()
                    return ClaimResult(action="already_sent", attempt_id=attempt.id)
                await session.commit()
                return ClaimResult(
                    action="blocked",
                    attempt_id=attempt.id,
                    reason=attempt.status,
                )
            if attempt.status == ATTEMPT_NEEDS_ATTENTION:
                await session.commit()
                return ClaimResult(
                    action="blocked",
                    attempt_id=attempt.id,
                    reason=attempt.status,
                )
            if attempt.status == ATTEMPT_SENDING:
                await session.commit()
                return ClaimResult(
                    action="blocked",
                    attempt_id=attempt.id,
                    reason="already_sending",
                )
            if (
                attempt.status == ATTEMPT_KNOWN_FAILED
                and attempt.next_attempt_at
                and attempt.next_attempt_at > now
            ):
                await session.commit()
                return ClaimResult(
                    action="blocked",
                    attempt_id=attempt.id,
                    reason="backoff",
                )

        if attempt.claim_token and attempt.status == ATTEMPT_SENDING:
            await session.commit()
            return ClaimResult(action="blocked", attempt_id=attempt.id, reason="active_claim")

        attempt.status = ATTEMPT_SENDING
        attempt.claim_owner = claim_owner
        attempt.claim_token = claim_token
        attempt.claim_expires_at = lease_until
        attempt.started_at = now
        attempt.attempt_count += 1
        await session.commit()
        return ClaimResult(
            action="proceed",
            attempt_id=attempt.id,
            claim_token=claim_token,
        )


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
        attempt = await session.get(SendAttempt, attempt_id)
        if not attempt:
            return
        if attempt.claim_token and attempt.claim_token != claim_token:
            return
        attempt.status = ATTEMPT_UNKNOWN_RESULT
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
        attempt = await session.get(SendAttempt, attempt_id)
        if not attempt:
            return False
        if attempt.claim_token != claim_token:
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

        if success:
            attempt.status = ATTEMPT_CONFIRMED_SENT
            attempt.claim_token = None
            await _apply_confirmed_send(session, attempt)
        elif ambiguous:
            attempt.status = ATTEMPT_UNKNOWN_RESULT
            attempt.claim_token = None
        else:
            attempt.status = ATTEMPT_KNOWN_FAILED
            attempt.claim_token = None
            if attempt.attempt_count >= settings.send_attempt_max_attempts:
                attempt.status = ATTEMPT_NEEDS_ATTENTION
            else:
                delay = compute_backoff_seconds(
                    attempt.attempt_count,
                    settings.send_attempt_base_backoff_seconds,
                )
                attempt.next_attempt_at = now + timedelta(seconds=delay)

        try:
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


async def _apply_confirmed_send(session, attempt: SendAttempt) -> None:
    if attempt.kind == ATTEMPT_KIND_PROPOSAL and attempt.proposal_id:
        proposal = await session.get(Proposal, attempt.proposal_id)
        if proposal:
            if (
                attempt.snapshot_version is not None
                and proposal.version != attempt.snapshot_version
            ):
                logger.warning(
                    "Proposal version changed after send snapshot",
                    proposal_id=str(proposal.id),
                    snapshot_version=attempt.snapshot_version,
                    current_version=proposal.version,
                )
            proposal.status = "sent"
            proposal.sent_at = datetime.now(UTC)
            proposal.content = attempt.snapshot_content
        if attempt.job_id:
            job = await session.get(Job, attempt.job_id)
            if job:
                job.status = "sent"
    elif attempt.kind == ATTEMPT_KIND_INBOX_REPLY and attempt.inbox_pending_id:
        pending = await session.get(TelegramInboxPending, attempt.inbox_pending_id)
        if pending:
            pending.status = "sent"
            pending.draft_reply = attempt.snapshot_content


async def prepare_proposal_send(
    job_id: str,
    proposal_id: str,
    content: str,
    *,
    allow_resubmit: bool = False,
    claim_owner: str = "proposal_sender",
) -> tuple[ClaimResult, Job | None, Proposal | None]:
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        proposal = await session.get(Proposal, uuid.UUID(proposal_id))
        if not job or not proposal:
            return ClaimResult(action="blocked", reason="not_found"), job, proposal
        if proposal.job_id != job.id:
            return ClaimResult(action="blocked", reason="job_proposal_mismatch"), job, proposal

        if proposal.status == "sent" and not allow_resubmit:
            if proposal.content.strip() == content.strip():
                return ClaimResult(action="unchanged"), job, proposal
            return ClaimResult(action="blocked", reason="already_sent_different_content"), job, proposal

        key = proposal_idempotency_key(proposal.id, proposal.version, content)
        metadata = {
            "platform": job.platform,
            "url": job.url,
            "budget_min": float(job.budget_min) if job.budget_min else None,
            "budget_max": float(job.budget_max) if job.budget_max else None,
        }
        claim = await claim_send_attempt(
            kind=ATTEMPT_KIND_PROPOSAL,
            idempotency_key=key,
            snapshot_content=content,
            snapshot_version=proposal.version,
            snapshot_metadata=metadata,
            job_id=job.id,
            proposal_id=proposal.id,
            claim_owner=claim_owner,
        )
        return claim, job, proposal


async def prepare_inbox_reply_send(
    *,
    pending_id: uuid.UUID,
    reply_text: str,
    expected_user_id: int,
    claim_owner: str = "inbox_reply",
) -> ClaimResult:
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, pending_id)
        if not pending:
            return ClaimResult(action="blocked", reason="pending_not_found")
        if pending.kwork_user_id != expected_user_id:
            return ClaimResult(action="blocked", reason="user_mismatch")
        if pending.status not in ("pending", "edited"):
            if pending.status == "sent":
                return ClaimResult(action="already_sent", reason="already_sent")
            return ClaimResult(action="blocked", reason=f"status_{pending.status}")
        job_id = pending.job_id
        proposal_id = pending.proposal_id
        username = pending.kwork_username

    key = inbox_reply_idempotency_key(pending_id, reply_text)
    return await claim_send_attempt(
        kind=ATTEMPT_KIND_INBOX_REPLY,
        idempotency_key=key,
        snapshot_content=reply_text,
        snapshot_version=None,
        snapshot_metadata={"username": username, "user_id": expected_user_id},
        job_id=job_id,
        proposal_id=proposal_id,
        inbox_pending_id=pending_id,
        claim_owner=claim_owner,
    )
