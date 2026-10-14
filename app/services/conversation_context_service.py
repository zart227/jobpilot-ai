"""Shared chat context for API /chat and Kwork inbox."""

from __future__ import annotations

import uuid
from typing import Any

from app.db.models import Client, Job, Proposal
from app.services.kwork_conversation import (
    resolve_conversation_context,
    resolve_conversation_history,
)
from app.services.kwork_job_match import best_proposal_for_reply


def proposal_text_for_chat(proposal: Proposal | None) -> str:
    if not proposal:
        return ""
    return (proposal.content or "").strip()


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


async def build_chat_agent_state(
    session,
    job_id: uuid.UUID,
    *,
    client_message: str = "",
    channel: str = "kwork",
    thread_messages: list[dict[str, Any]] | None = None,
    my_username: str = "",
    my_user_id: int | None = None,
    proposal: Proposal | None = None,
) -> dict:
    job = await session.get(Job, job_id)
    if not job:
        return {}

    if proposal is None:
        proposal = await load_reply_proposal(session, job_id)

    conversation_history = await resolve_conversation_history(
        session,
        job,
        client_message=client_message,
        channel=channel,
        thread_messages=thread_messages,
        my_username=my_username,
        my_user_id=my_user_id,
    )
    conversation_context = await resolve_conversation_context(
        session,
        job,
        client_message=client_message,
        channel=channel,
        thread_messages=thread_messages,
        my_username=my_username,
        my_user_id=my_user_id,
    )

    return {
        "job_id": str(job.id),
        "job_data": await _job_data_dict(session, job),
        "client_message": client_message,
        "proposal_content": proposal_text_for_chat(proposal),
        "proposal_id": str(proposal.id) if proposal else None,
        "conversation_history": conversation_history,
        "conversation_context": conversation_context,
    }
