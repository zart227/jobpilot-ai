"""Exact inbox presentation and human authorization, separate immutable records."""

from sqlalchemy import select

from app.db.models import InboxApprovalRecord


def inbox_payload(pending) -> dict:
    return {
        "schema_version": 1,
        "review_format": "full_text_v1",
        "pending_id": str(pending.id),
        "interaction_id": str(pending.interaction_id) if pending.interaction_id else None,
        "job_id": str(pending.job_id) if pending.job_id else None,
        "proposal_id": str(pending.proposal_id) if pending.proposal_id else None,
        "user_id": pending.kwork_user_id,
        "username": pending.kwork_username,
        "client_message": pending.client_message,
        "content": pending.draft_reply,
    }


async def capture_inbox_presentation(session, pending):
    record = InboxApprovalRecord(
        pending_id=pending.id,
        payload=inbox_payload(pending),
        record_kind="presented",
        chat_id=pending.chat_id,
    )
    session.add(record)
    await session.flush()
    return record


async def inbox_presentation_is_current(session, pending):
    record = await session.scalar(
        select(InboxApprovalRecord)
        .where(
            InboxApprovalRecord.pending_id == pending.id,
            InboxApprovalRecord.record_kind == "presented",
        )
        .order_by(InboxApprovalRecord.created_at.desc())
        .limit(1)
    )
    return record is not None and record.payload == inbox_payload(pending)


async def record_inbox_approval(
    session, pending, *, actor_user_id, chat_id, message_id, edited=False
):
    if actor_user_id <= 0 or not chat_id or message_id <= 0:
        raise ValueError("Missing approval identity")
    if pending.chat_id != chat_id or pending.message_id != message_id:
        raise ValueError("Approval card identity mismatch")
    presentation = await session.scalar(
        select(InboxApprovalRecord)
        .where(
            InboxApprovalRecord.pending_id == pending.id,
            InboxApprovalRecord.record_kind == "presented",
        )
        .order_by(InboxApprovalRecord.created_at.desc())
        .limit(1)
    )
    payload = inbox_payload(pending)
    if presentation is None:
        raise ValueError("Missing inbox presentation")
    expected = dict(presentation.payload)
    if edited:
        expected["content"] = payload["content"]
    if expected != payload or presentation.chat_id != chat_id:
        raise ValueError("Stale inbox presentation")
    record = InboxApprovalRecord(
        pending_id=pending.id,
        payload=payload,
        record_kind="edited_preview" if edited else "card",
        actor_user_id=actor_user_id,
        chat_id=chat_id,
        message_id=message_id,
    )
    session.add(record)
    await session.flush()
    return record


async def matching_inbox_approval(session, pending):
    return await session.scalar(
        select(InboxApprovalRecord)
        .where(
            InboxApprovalRecord.pending_id == pending.id,
            InboxApprovalRecord.record_kind.in_(["card", "edited_preview"]),
            InboxApprovalRecord.actor_user_id > 0,
            InboxApprovalRecord.chat_id == pending.chat_id,
            InboxApprovalRecord.message_id == pending.message_id,
            InboxApprovalRecord.payload == inbox_payload(pending),
        )
        .order_by(InboxApprovalRecord.created_at.desc())
        .limit(1)
    )
