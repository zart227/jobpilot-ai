"""Shared chat context for API /chat and Kwork inbox."""

from __future__ import annotations

import uuid
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import func, select

from app.db.models import Client, Job, Proposal, SendAttempt
from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT, ATTEMPT_KIND_PROPOSAL
from app.db.session import AsyncSessionLocal
from app.services.conversation_summary import SUMMARY_CACHE_KEY, build_conversation_context
from app.services.kwork_conversation import (
    conversation_summary_thread_scope,
    resolve_conversation_context,
    resolve_conversation_history,
)
from app.services.kwork_job_match import best_proposal_for_reply

logger = structlog.get_logger(__name__)


async def _job_data_dict(session, job: Job) -> dict:
    client_name = None
    client_rating = None
    client_reviews = 0
    client_metadata: dict = {}
    if job.client_id:
        client = await session.get(Client, job.client_id)
        if client:
            client_name = client.name
            client_rating = float(client.rating) if client.rating else None
            client_reviews = client.reviews_count
            client_metadata = client.metadata_ or {}

    return {
        "id": str(job.id),
        "platform": job.platform,
        "external_id": job.external_id,
        "title": job.title,
        "description": job.description,
        "budget_min": float(job.budget_min) if job.budget_min else None,
        "budget_max": float(job.budget_max) if job.budget_max else None,
        "budget_currency": job.budget_currency,
        "skills": job.skills or [],
        "deadline": job.deadline.isoformat() if job.deadline else None,
        "url": job.url,
        "client_name": client_name,
        "client_rating": client_rating,
        "client_reviews": client_reviews,
        "client_completed_paid_orders": client_metadata.get("completed_paid_orders"),
    }


async def load_reply_proposal(session, job_id: uuid.UUID) -> Proposal | None:
    return await best_proposal_for_reply(session, job_id)


async def _confirmed_proposal_for_chat(
    session, job: Job, proposal: Proposal | None
) -> SendAttempt | None:
    if proposal is not None and proposal.job_id != job.id:
        return None
    criteria = [
        SendAttempt.job_id == job.id,
        SendAttempt.kind == ATTEMPT_KIND_PROPOSAL,
        SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
        func.coalesce(SendAttempt.platform_response["dry_run"].astext, "false") != "true",
    ]
    if proposal is not None:
        criteria.append(SendAttempt.proposal_id == proposal.id)
    return await session.scalar(
        select(SendAttempt)
        .where(*criteria)
        .order_by(
            SendAttempt.finished_at.desc().nulls_last(),
            SendAttempt.created_at.desc(),
            SendAttempt.id.desc(),
        )
        .limit(1)
    )


async def build_chat_agent_state(
    session,
    job_id: uuid.UUID,
    *,
    client_message: str = "",
    channel: str = "kwork",
    thread_messages: list[dict[str, Any]] | None = None,
    my_username: str = "",
    my_user_id: int | None = None,
    client_message_id: str | None = None,
    proposal: Proposal | None = None,
) -> dict:
    job = await session.get(Job, job_id)
    if not job:
        return {}

    sent_attempt = await _confirmed_proposal_for_chat(session, job, proposal)
    summary_thread_scope = conversation_summary_thread_scope(
        channel=channel,
        job_id=job.id,
        thread_messages=thread_messages,
        client_message_id=client_message_id,
    )

    conversation_history = await resolve_conversation_history(
        session,
        job,
        client_message=client_message,
        channel=channel,
        thread_messages=thread_messages,
        my_username=my_username,
        my_user_id=my_user_id,
        client_message_id=client_message_id,
    )
    conversation_context = await resolve_conversation_context(
        session,
        job,
        client_message=client_message,
        channel=channel,
        thread_messages=thread_messages,
        my_username=my_username,
        my_user_id=my_user_id,
        client_message_id=client_message_id,
        thread_scope=summary_thread_scope,
    )

    return {
        "job_id": str(job.id),
        "job_data": await _job_data_dict(session, job),
        "client_message": client_message,
        "proposal_content": sent_attempt.snapshot_content if sent_attempt else "",
        "proposal_id": str(sent_attempt.proposal_id) if sent_attempt and sent_attempt.proposal_id else None,
        "conversation_history": conversation_history,
        "conversation_context": conversation_context,
        "_summary_thread_scope": summary_thread_scope,
        "_summary_cache": deepcopy((job.raw_data or {}).get(SUMMARY_CACHE_KEY)),
    }


async def finalize_chat_agent_state(state: dict) -> dict:
    """Summarize outside the caller's DB transaction, then cache in a short guarded TX."""
    if not state or "_summary_cache" not in state:
        return state
    old_cache = state.pop("_summary_cache")
    holder = SimpleNamespace(raw_data={SUMMARY_CACHE_KEY: deepcopy(old_cache)})
    state["conversation_context"] = await build_conversation_context(
        None,
        holder,
        state.get("conversation_history") or [],
        thread_scope=state.get("_summary_thread_scope"),
    )
    new_cache = holder.raw_data.get(SUMMARY_CACHE_KEY)
    if new_cache != old_cache:
        try:
            async with AsyncSessionLocal() as session:
                job = await session.scalar(
                    select(Job).where(Job.id == uuid.UUID(state["job_id"])).with_for_update()
                )
                if job and (job.raw_data or {}).get(SUMMARY_CACHE_KEY) == old_cache:
                    job.raw_data = {**(job.raw_data or {}), SUMMARY_CACHE_KEY: new_cache}
                    await session.commit()
        except Exception as exc:
            logger.warning("Conversation summary cache save failed", error=str(exc))
    return state
