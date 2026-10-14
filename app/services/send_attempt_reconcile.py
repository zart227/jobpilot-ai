"""Read-only reconciliation for ambiguous external sends (unknown_result)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog

from app.config import Settings, get_settings
from app.db.models import Job, SendAttempt
from app.db.send_attempt_states import (
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KIND_INBOX_REPLY,
    ATTEMPT_KIND_PROPOSAL,
    ATTEMPT_NEEDS_ATTENTION,
    ATTEMPT_UNKNOWN_RESULT,
)
from app.db.session import AsyncSessionLocal
from app.services.kwork_api import clean_kwork_message_text, create_kwork_api
from app.services.kwork_conversation import is_from_client
from app.services.send_attempt_service import content_fingerprint
from app.utils.formatting import extract_site_order_id, sanitize_proposal_text

logger = structlog.get_logger(__name__)

ReconcileOutcome = Literal["confirmed", "not_found", "inconclusive", "skipped"]


def outbound_reply_visible_in_thread(
    messages: list[dict[str, Any]],
    *,
    expected_text: str,
    my_user_id: int | None,
    my_username: str,
) -> bool:
    """True when our snapshot text appears in a non-client (freelancer) message."""
    needle = sanitize_proposal_text(expected_text).strip()
    if not needle:
        return False
    needle_fp = content_fingerprint(needle)
    for message in messages:
        if is_from_client(message, my_username, my_user_id):
            continue
        text = clean_kwork_message_text(
            str(message.get("message", message.get("text", "")))
        ).strip()
        if not text:
            continue
        if text == needle or content_fingerprint(text) == needle_fp:
            return True
        if len(needle) >= 40 and needle[:40] in text:
            return True
    return False


async def _confirm_reconciled_send(
    attempt_id: uuid.UUID,
    *,
    source: str,
    evidence: dict[str, Any] | None = None,
) -> bool:
    from app.services.send_attempt_service import _apply_confirmed_send

    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        if not attempt or attempt.status != ATTEMPT_UNKNOWN_RESULT:
            return False
        merged = dict(attempt.platform_response or {})
        merged["reconciliation"] = {"source": source, **(evidence or {})}
        attempt.platform_response = merged
        attempt.status = ATTEMPT_CONFIRMED_SENT
        attempt.last_error = None
        await _apply_confirmed_send(session, attempt)
        await session.commit()
        logger.info(
            "Send attempt reconciled to confirmed_sent",
            attempt_id=str(attempt_id),
            source=source,
            kind=attempt.kind,
        )
        return True


async def _reconcile_inbox_reply(attempt: SendAttempt, settings: Settings) -> ReconcileOutcome:
    meta = attempt.snapshot_metadata or {}
    username = str(meta.get("username") or "")
    if not username:
        return "inconclusive"

    api = None
    try:
        api = await create_kwork_api(settings)
        actor = await api.get_actor()
        my_user_id = int(actor.get("id") or 0) or None
        my_username = str(actor.get("username") or "")
        messages = await api.get_messages(username)
    except Exception as exc:
        logger.warning(
            "Inbox reconcile fetch failed",
            attempt_id=str(attempt.id),
            username=username,
            error=str(exc),
        )
        return "inconclusive"
    finally:
        if api is not None:
            await api.close()

    if outbound_reply_visible_in_thread(
        messages,
        expected_text=attempt.snapshot_content,
        my_user_id=my_user_id,
        my_username=my_username,
    ):
        ok = await _confirm_reconciled_send(
            attempt.id,
            source="kwork_inbox_thread",
            evidence={"username": username},
        )
        return "confirmed" if ok else "inconclusive"
    return "not_found"


async def _reconcile_kwork_proposal(attempt: SendAttempt, job: Job, settings: Settings) -> ReconcileOutcome:
    if settings.platform_dry_run:
        if (attempt.platform_response or {}).get("dry_run"):
            ok = await _confirm_reconciled_send(attempt.id, source="dry_run_metadata")
            return "confirmed" if ok else "inconclusive"
        return "inconclusive"

    project_id = extract_site_order_id(
        external_id=job.external_id,
        url=job.url,
        platform=job.platform,
    )
    if not project_id:
        return "inconclusive"

    from app.services.kwork_browser import verify_kwork_offer_submitted

    try:
        found = await verify_kwork_offer_submitted(job.url or f"https://kwork.ru/projects/{project_id}/view")
    except Exception as exc:
        logger.warning(
            "Proposal reconcile browser check failed",
            attempt_id=str(attempt.id),
            project_id=project_id,
            error=str(exc),
        )
        return "inconclusive"

    if found:
        ok = await _confirm_reconciled_send(
            attempt.id,
            source="kwork_project_page",
            evidence={"project_id": project_id},
        )
        return "confirmed" if ok else "inconclusive"
    return "not_found"


async def reconcile_send_attempt(
    attempt_id: uuid.UUID,
    *,
    settings: Settings | None = None,
) -> ReconcileOutcome:
    settings = settings or get_settings()
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        if not attempt:
            return "skipped"
        if attempt.status != ATTEMPT_UNKNOWN_RESULT:
            return "skipped"
        job = await session.get(Job, attempt.job_id) if attempt.job_id else None

    if attempt.kind == ATTEMPT_KIND_INBOX_REPLY:
        return await _reconcile_inbox_reply(attempt, settings)

    if attempt.kind == ATTEMPT_KIND_PROPOSAL and job and job.platform == "kwork":
        return await _reconcile_kwork_proposal(attempt, job, settings)

    return "inconclusive"


async def mark_inconclusive_needs_attention(attempt_id: uuid.UUID, reason: str) -> bool:
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        if not attempt or attempt.status != ATTEMPT_UNKNOWN_RESULT:
            return False
        attempt.status = ATTEMPT_NEEDS_ATTENTION
        attempt.last_error = reason[:2000]
        await session.commit()
        return True
