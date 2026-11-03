"""Confirmed inbox sends and history are one transaction, including reconciliation."""

import os
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Interaction, SendAttempt, TelegramInboxPending
from app.db.session import AsyncSessionLocal
from app.services import send_attempt_service
from app.services.send_attempt_reconcile import _confirm_reconciled_send

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated database required"
)

CONTENT = "  Exact sent reply\nwith whitespace  "


async def make_attempt(status="sending", *, pending_content=CONTENT):
    pending_id, attempt_id, token = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TelegramInboxPending(
            id=pending_id, kwork_user_id=42, kwork_username="buyer", client_message="question",
            draft_reply=pending_content, chat_id=7, status="approved",
        ))
        await session.flush()
        session.add(SendAttempt(
            id=attempt_id, kind="inbox_reply", idempotency_key=f"history:{attempt_id}",
            inbox_pending_id=pending_id, snapshot_content=CONTENT, status=status,
            claim_token=token if status == "sending" else None,
            snapshot_metadata={"username": "buyer", "user_id": 42, "approved_payload": {
                "interaction_id": None, "content": CONTENT, "username": "buyer", "user_id": 42,
            }},
        ))
        await session.commit()
    return pending_id, attempt_id, token


async def histories(session, attempt_id):
    return list((await session.scalars(select(Interaction).where(
        Interaction.metadata_["send_attempt_id"].astext == str(attempt_id),
        Interaction.direction == "outbound",
    ))).all())


async def test_success_finalization_includes_exact_history_and_is_idempotent():
    pending_id, attempt_id, token = await make_attempt()
    assert await send_attempt_service.finalize_send_attempt(attempt_id, token, success=True)
    assert not await send_attempt_service.finalize_send_attempt(attempt_id, token, success=True)
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        pending = await session.get(TelegramInboxPending, pending_id)
        items = await histories(session, attempt_id)
        assert attempt.status == "confirmed_sent" and pending.status == "sent"
        assert len(items) == 1 and items[0].message == CONTENT
        assert items[0].metadata_["pending_id"] == str(pending_id)


async def test_reconcile_confirm_writes_history_once():
    pending_id, attempt_id, _ = await make_attempt("unknown_result")
    assert await _confirm_reconciled_send(attempt_id, source="synthetic_receipt")
    assert not await _confirm_reconciled_send(attempt_id, source="synthetic_receipt")
    async with AsyncSessionLocal() as session:
        assert (await session.get(TelegramInboxPending, pending_id)).status == "sent"
        items = await histories(session, attempt_id)
        assert len(items) == 1 and items[0].message == CONTENT


async def test_confirmed_snapshot_is_recorded_even_if_draft_changed():
    pending_id, attempt_id, token = await make_attempt(pending_content="new draft")
    assert await send_attempt_service.finalize_send_attempt(attempt_id, token, success=True)
    async with AsyncSessionLocal() as session:
        assert (await session.get(TelegramInboxPending, pending_id)).status == "approved"
        items = await histories(session, attempt_id)
        assert len(items) == 1 and items[0].message == CONTENT


async def test_whitespace_only_draft_change_does_not_mark_new_draft_sent():
    pending_id, attempt_id, token = await make_attempt(pending_content=CONTENT.strip())
    assert await send_attempt_service.finalize_send_attempt(attempt_id, token, success=True)
    async with AsyncSessionLocal() as session:
        assert (await session.get(TelegramInboxPending, pending_id)).status == "approved"
        assert (await histories(session, attempt_id))[0].message == CONTENT


async def test_history_write_error_leaves_uncertain_attempt_without_sent_pending(monkeypatch):
    pending_id, attempt_id, token = await make_attempt()

    async def fail_history(*args, **kwargs):
        raise SQLAlchemyError("synthetic history failure")

    monkeypatch.setattr(send_attempt_service, "_ensure_inbox_outbound_interaction", fail_history, raising=False)
    assert not await send_attempt_service.finalize_send_attempt(attempt_id, token, success=True)
    async with AsyncSessionLocal() as session:
        assert (await session.get(SendAttempt, attempt_id)).status == "unknown_result"
        assert (await session.get(TelegramInboxPending, pending_id)).status == "approved"
        assert await histories(session, attempt_id) == []


async def test_existing_legacy_history_is_not_duplicated_by_reconcile():
    _, attempt_id, _ = await make_attempt("unknown_result")
    async with AsyncSessionLocal() as session:
        session.add(Interaction(
            direction="outbound", channel="kwork", message=CONTENT,
            intent="reply_sent", metadata_={"send_attempt_id": str(attempt_id)},
        ))
        await session.commit()
    assert await _confirm_reconciled_send(attempt_id, source="synthetic_receipt")
    async with AsyncSessionLocal() as session:
        assert len(await histories(session, attempt_id)) == 1


async def test_database_failure_in_history_insert_rolls_back_and_marks_unknown():
    pending_id, attempt_id, token = await make_attempt()

    def fail_insert(*args):
        raise SQLAlchemyError("synthetic insert failure")

    event.listen(Interaction, "before_insert", fail_insert)
    try:
        assert not await send_attempt_service.finalize_send_attempt(attempt_id, token, success=True)
    finally:
        event.remove(Interaction, "before_insert", fail_insert)
    async with AsyncSessionLocal() as session:
        assert (await session.get(SendAttempt, attempt_id)).status == "unknown_result"
        assert (await session.get(TelegramInboxPending, pending_id)).status == "approved"
        assert not await histories(session, attempt_id)
    assert await _confirm_reconciled_send(attempt_id, source="synthetic_receipt")
    async with AsyncSessionLocal() as session:
        assert len(await histories(session, attempt_id)) == 1


async def test_reconcile_insert_failure_keeps_unknown_until_successful_retry():
    pending_id, attempt_id, _ = await make_attempt("unknown_result")

    def fail_insert(*args):
        raise SQLAlchemyError("synthetic insert failure")

    event.listen(Interaction, "before_insert", fail_insert)
    try:
        with pytest.raises(SQLAlchemyError):
            await _confirm_reconciled_send(attempt_id, source="synthetic_receipt")
    finally:
        event.remove(Interaction, "before_insert", fail_insert)
    async with AsyncSessionLocal() as session:
        assert (await session.get(SendAttempt, attempt_id)).status == "unknown_result"
        assert (await session.get(TelegramInboxPending, pending_id)).status == "approved"
        assert not await histories(session, attempt_id)
    assert await _confirm_reconciled_send(attempt_id, source="synthetic_receipt")
    async with AsyncSessionLocal() as session:
        assert len(await histories(session, attempt_id)) == 1
