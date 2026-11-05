"""Queue a fresh human review without authorizing or retrying external delivery."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, update

from app.config import Settings, get_settings
from app.db.models import (
    InboundMessage,
    Interaction,
    Job,
    NotificationOutbox,
    Proposal,
    TelegramInboxPending,
    TelegramPending,
)
from app.db.session import AsyncSessionLocal
from app.services.approval_snapshot import capture_proposal_card
from app.services.inbox_approval import capture_inbox_presentation
from app.services.notification_outbox import enqueue_notification
from app.services.send_attempt_service import delivery_blocks_mutation
from app.telegram.access import is_authorized_telegram
from app.telegram.full_review import REVIEW_FORMAT

ReviewKind = Literal["proposal", "inbox"]


@dataclass(frozen=True)
class ReviewReissueResult:
    action: Literal["queued", "already_queued", "blocked"]
    pending_id: uuid.UUID | None = None
    outbox_id: uuid.UUID | None = None
    reason: str | None = None


async def review_reference_for_message(
    chat_id: int,
    message_id: int,
    *,
    user_id: int,
    settings: Settings | None = None,
) -> tuple[ReviewKind, uuid.UUID] | None:
    if not is_authorized_telegram(chat_id, user_id, settings):
        return None
    matches = []
    async with AsyncSessionLocal() as session:
        for kind, model in [("proposal", TelegramPending), ("inbox", TelegramInboxPending)]:
            ids = await session.scalars(
                select(model.id)
                .where(
                    model.chat_id == chat_id,
                    model.message_id == message_id,
                )
                .limit(2)
            )
            matches.extend((kind, value) for value in ids)
    return matches[0] if len(matches) == 1 else None


async def _lock(session, model, row_id):
    return await session.scalar(
        select(model)
        .where(model.id == row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _queue_proposal(session, source, job, proposal, key, user_id):
    pending = TelegramPending(
        job_id=job.id, proposal_id=proposal.id, chat_id=source.chat_id, status="pending"
    )
    session.add(pending)
    await session.flush()
    snapshot = await capture_proposal_card(session, pending.id, job, proposal)
    payload = snapshot.payload
    heading = (
        f"Повторный review: {job.title[:300]}\n{job.url or ''}\n"
        f"Версия: {proposal.version}; оценка: {job.score}\n"
        f"Цена: {payload['offer_price']} {payload['currency']}; "
        f"срок: {payload['duration_days']} дней"
    )
    outbox = await enqueue_notification(
        kind="proposal_approval",
        idempotency_key=key,
        chat_id=pending.chat_id,
        payload={
            "pending_id": str(pending.id),
            "job_id": str(job.id),
            "proposal_id": str(proposal.id),
            "alert_text": heading,
            "review_heading": heading,
            "review_content": payload["content"],
            "review_format": REVIEW_FORMAT,
            "reissued_from": str(source.id),
            "requested_by": user_id,
            "source_status": source.status,
            "source_message_id": source.message_id,
        },
        reference_type="telegram_pending",
        reference_id=pending.id,
        session=session,
    )
    # Re-review revokes mutable readiness; it never grants approval.
    proposal.status, proposal.approved_at = "pending_approval", None
    job.status = "pending_approval"
    return pending, outbox


async def _queue_inbox(session, source, key, user_id):
    pending = TelegramInboxPending(
        interaction_id=source.interaction_id,
        job_id=source.job_id,
        proposal_id=source.proposal_id,
        kwork_user_id=source.kwork_user_id,
        kwork_username=source.kwork_username,
        client_message=source.client_message,
        draft_reply=source.draft_reply,
        chat_intent=source.chat_intent,
        chat_id=source.chat_id,
        status="pending",
    )
    session.add(pending)
    await session.flush()
    await capture_inbox_presentation(session, pending)
    heading = (
        f"Повторный review: ответ клиенту {pending.kwork_username[:150]} "
        f"(user {pending.kwork_user_id})\nЗаказ: {pending.job_id or 'не определён'}\n"
        f"Входящее сообщение:\n{pending.client_message}"
    )
    outbox = await enqueue_notification(
        kind="inbox_alert",
        idempotency_key=key,
        chat_id=pending.chat_id,
        payload={
            "pending_id": str(pending.id),
            "alert_text": heading,
            "review_heading": heading,
            "review_content": pending.draft_reply,
            "review_format": REVIEW_FORMAT,
            "reissued_from": str(source.id),
            "requested_by": user_id,
            "source_status": source.status,
            "source_message_id": source.message_id,
        },
        reference_type="telegram_inbox_pending",
        reference_id=pending.id,
        session=session,
    )
    await session.execute(
        update(InboundMessage)
        .where(InboundMessage.inbox_pending_id == source.id)
        .values(inbox_pending_id=pending.id)
    )
    return pending, outbox


async def reissue_review(
    kind: ReviewKind,
    pending_id: uuid.UUID,
    *,
    chat_id: int,
    user_id: int,
    source_message_id: int | None = None,
    settings: Settings | None = None,
) -> ReviewReissueResult:
    settings = settings or get_settings()
    if not is_authorized_telegram(chat_id, user_id, settings):
        return ReviewReissueResult("blocked", reason="unauthorized")
    if kind not in ("proposal", "inbox"):
        return ReviewReissueResult("blocked", reason="invalid_kind")
    model = TelegramPending if kind == "proposal" else TelegramInboxPending
    key = f"review_reissue:{kind}:{pending_id}"
    async with AsyncSessionLocal() as session:
        source = await session.get(model, pending_id)
        if not source:
            return ReviewReissueResult("blocked", reason="not_found")
        job = proposal = None
        if kind == "proposal":
            refs = source.job_id, source.proposal_id
            # Same order as send/finalize/edit; no network within this transaction.
            job = await _lock(session, Job, refs[0])
            proposal = await _lock(session, Proposal, refs[1])
            source = await _lock(session, model, pending_id)
            if (
                not source
                or not job
                or not proposal
                or (source.job_id, source.proposal_id) != refs
                or proposal.job_id != job.id
            ):
                return ReviewReissueResult("blocked", reason="invalid_target")
        else:
            source = await _lock(session, model, pending_id)
        if not source or source.chat_id != chat_id:
            return ReviewReissueResult("blocked", reason="chat_mismatch")
        if source_message_id is not None and source.message_id != source_message_id:
            return ReviewReissueResult("blocked", reason="stale_card")
        existing = await session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.idempotency_key == key)
        )
        if existing:
            return ReviewReissueResult("already_queued", existing.reference_id, existing.id)
        if source.status not in ("pending", "approved", "edited"):
            return ReviewReissueResult("blocked", reason="protected_status")
        if kind == "proposal":
            from app.services.approval_snapshot import proposal_card_payload
            from app.services.opportunity_policy import kwork_job_assessment_rejection
            from app.utils.formatting import kwork_text_price_matches

            policy_error = await kwork_job_assessment_rejection(session, job)
            if policy_error:
                return ReviewReissueResult("blocked", reason=policy_error)
            if job.platform == "kwork" and not kwork_text_price_matches(
                proposal.content, proposal_card_payload(job, proposal)["offer_price"]
            ):
                return ReviewReissueResult("blocked", reason="price_text_mismatch")
            from app.db.workflow_states import (
                JOB_STATUS_SIMULATED_SENT,
                PROPOSAL_STATUS_SENT,
                PROPOSAL_STATUS_SIMULATED_SENT,
            )
            from app.services.submission_policy import job_has_live_submitted_proposal

            sent = await session.scalar(
                select(Proposal.id)
                .where(
                    Proposal.job_id == job.id,
                    Proposal.status == PROPOSAL_STATUS_SENT,
                )
                .limit(1)
            )
            allowed_job_statuses = {
                "new",
                "scored",
                "pending_approval",
                "approved",
                "edited",
                JOB_STATUS_SIMULATED_SENT,
            }
            allowed_proposal_statuses = {
                "draft",
                "pending_approval",
                "approved",
                "edited",
                PROPOSAL_STATUS_SIMULATED_SENT,
            }
            if (
                sent
                or await job_has_live_submitted_proposal(session, job.id)
                or job.status not in allowed_job_statuses
                or proposal.status not in allowed_proposal_statuses
            ):
                return ReviewReissueResult("blocked", reason="already_sent_or_protected")
            if await delivery_blocks_mutation(session, job_id=job.id, include_confirmed=True):
                from app.db.models import SendAttempt
                from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
                from app.services.delivery_scope import is_live_confirmed_send_attempt

                rows = (
                    await session.scalars(
                        select(SendAttempt).where(
                            SendAttempt.job_id == job.id,
                            SendAttempt.status.in_(
                                (
                                    ATTEMPT_CONFIRMED_SENT,
                                    "sending",
                                    "unknown_result",
                                    "needs_attention",
                                )
                            ),
                        )
                    )
                ).all()
                if any(
                    row.status != ATTEMPT_CONFIRMED_SENT or is_live_confirmed_send_attempt(row)
                    for row in rows
                ):
                    return ReviewReissueResult("blocked", reason="delivery_requires_resolution")
            pending, outbox = await _queue_proposal(session, source, job, proposal, key, user_id)
        else:
            sent = await session.scalar(
                select(Interaction.id)
                .where(
                    Interaction.direction == "outbound",
                    Interaction.channel == "kwork",
                    Interaction.metadata_["pending_id"].astext == str(source.id),
                )
                .limit(1)
            )
            if sent or await delivery_blocks_mutation(
                session, inbox_pending_id=source.id, include_confirmed=True
            ):
                return ReviewReissueResult("blocked", reason="delivery_requires_resolution")
            pending, outbox = await _queue_inbox(session, source, key, user_id)
        from app.services.operator_override_journal import record_operator_override

        await record_operator_override(
            session,
            action="review_reissue",
            actor_source="telegram",
            actor_user_id=user_id,
            reference_type=f"telegram_{kind}_pending",
            reference_id=source.id,
            detail={
                "kind": kind,
                "new_pending_id": str(pending.id),
                "outbox_id": str(outbox.id),
                "source_status": source.status,
            },
        )
        source.status = "superseded"
        await session.commit()
        return ReviewReissueResult("queued", pending.id, outbox.id)


async def queue_edit_existing_proposal_review(
    *,
    pending_id: uuid.UUID,
    chat_id: int,
    user_id: int,
    source_message_id: int | None = None,
    settings: Settings | None = None,
) -> ReviewReissueResult:
    """Queue a fresh full-text review card authorized to edit an already-submitted offer."""
    from app.services.submission_policy import job_has_submitted_proposal

    settings = settings or get_settings()
    if not is_authorized_telegram(chat_id, user_id, settings):
        return ReviewReissueResult("blocked", reason="unauthorized")
    key = f"review_edit_existing:proposal:{pending_id}"
    async with AsyncSessionLocal() as session:
        source = await _lock(session, TelegramPending, pending_id)
        if not source or source.chat_id != chat_id:
            return ReviewReissueResult("blocked", reason="chat_mismatch")
        if source_message_id is not None and source.message_id != source_message_id:
            return ReviewReissueResult("blocked", reason="stale_card")
        existing = await session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.idempotency_key == key)
        )
        if existing:
            return ReviewReissueResult("already_queued", existing.reference_id, existing.id)
        job = await _lock(session, Job, source.job_id)
        proposal = await _lock(session, Proposal, source.proposal_id)
        if not job or not proposal or proposal.job_id != job.id:
            return ReviewReissueResult("blocked", reason="invalid_target")
        if not await job_has_submitted_proposal(session, job.id):
            return ReviewReissueResult("blocked", reason="no_prior_send")
        if await delivery_blocks_mutation(session, job_id=job.id, include_confirmed=True):
            return ReviewReissueResult("blocked", reason="delivery_requires_resolution")
        pending, outbox = await _queue_proposal(session, source, job, proposal, key, user_id)
        await session.commit()
        return ReviewReissueResult("queued", pending.id, outbox.id)
