"""Kwork thread / DB interaction history for chat context."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.db.models import Interaction, Job
from app.services.conversation_summary import build_conversation_context
from app.services.kwork_api import clean_kwork_message_text
from app.utils.formatting import sanitize_proposal_text

SYSTEM_MESSAGE_TYPES = frozenset(
    {
        "offer_kwork_new",
        "offer_kwork_done",
        "order_created",
        "order_completed",
        "order_cancelled",
        "custom_request",
    }
)


def _message_timestamp(message: dict[str, Any]) -> int:
    try:
        return int(message.get("time") or message.get("sent_timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def is_from_client(message: dict[str, Any], my_username: str, my_user_id: int | None) -> bool:
    msg_type = str(message.get("type", "text"))
    if msg_type in SYSTEM_MESSAGE_TYPES:
        return False

    sender_id = message.get("from_id") or message.get("user_from_id")
    if my_user_id and sender_id and int(sender_id) == int(my_user_id):
        return False

    sender = str(message.get("from_username", message.get("sender", ""))).lower()
    if my_username and sender == my_username.lower():
        return False

    text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
    return bool(text.strip())


def conversation_from_kwork_thread(
    messages: list[dict[str, Any]] | None,
    *,
    my_username: str,
    my_user_id: int | None,
    exclude_last_client_text: str | None = None,
) -> list[dict[str, str]]:
    if not messages:
        return []

    turns: list[dict[str, str]] = []
    for message in sorted(messages, key=_message_timestamp):
        msg_type = str(message.get("type", "text"))
        if msg_type in SYSTEM_MESSAGE_TYPES:
            continue
        text = clean_kwork_message_text(str(message.get("message", message.get("text", ""))))
        if not text.strip():
            continue
        role = "client" if is_from_client(message, my_username, my_user_id) else "freelancer"
        turns.append({"role": role, "text": text})

    if exclude_last_client_text and turns:
        last = turns[-1]
        if last["role"] == "client" and last["text"].strip() == exclude_last_client_text.strip():
            turns = turns[:-1]
    return turns


def conversation_from_interactions(
    interactions: list[Interaction],
    *,
    exclude_last_client_text: str | None = None,
) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for row in interactions:
        if row.direction == "outbound" and (row.metadata_ or {}).get("dry_run") is True:
            continue
        text = sanitize_proposal_text(row.message).strip()
        if not text:
            continue
        role = "client" if row.direction == "inbound" else "freelancer"
        turns.append({"role": role, "text": text})

    if exclude_last_client_text and turns:
        last = turns[-1]
        if last["role"] == "client" and last["text"].strip() == exclude_last_client_text.strip():
            turns = turns[:-1]
    return turns


async def load_db_interactions(session, job_id: uuid.UUID, channel: str = "kwork") -> list[Interaction]:
    result = await session.execute(
        select(Interaction)
        .where(Interaction.job_id == job_id, Interaction.channel == channel)
        .order_by(Interaction.created_at.asc())
    )
    return list(result.scalars().all())


async def resolve_conversation_history(
    session,
    job: Job,
    *,
    client_message: str,
    channel: str = "kwork",
    thread_messages: list[dict[str, Any]] | None = None,
    my_username: str = "",
    my_user_id: int | None = None,
) -> list[dict[str, str]]:
    if channel == "kwork" and thread_messages:
        thread_history = conversation_from_kwork_thread(
            thread_messages,
            my_username=my_username,
            my_user_id=my_user_id,
            exclude_last_client_text=client_message,
        )
        if thread_history:
            return thread_history

    interactions = await load_db_interactions(session, job.id, channel=channel)
    return conversation_from_interactions(
        interactions,
        exclude_last_client_text=client_message,
    )


async def resolve_conversation_context(
    session,
    job: Job,
    *,
    client_message: str,
    channel: str = "kwork",
    thread_messages: list[dict[str, Any]] | None = None,
    my_username: str = "",
    my_user_id: int | None = None,
) -> str:
    turns = await resolve_conversation_history(
        session,
        job,
        client_message=client_message,
        channel=channel,
        thread_messages=thread_messages,
        my_username=my_username,
        my_user_id=my_user_id,
    )
    return await build_conversation_context(session, job, turns)
