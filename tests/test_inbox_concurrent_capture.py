"""Two pollers seeing one message must not abort the cycle on unique conflict."""

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import InboundMessage, Interaction, NotificationOutbox
from app.db.session import AsyncSessionLocal
from app.services import kwork_inbox

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


def make_message():
    message_id = uuid.uuid4().hex
    return kwork_inbox.ClientInboxMessage(
        user_id=int(uuid.uuid4().int % 800_000_000) + 100,
        username="synthetic", message_id=message_id, text="synthetic inbound",
        timestamp=1, dialog={}, raw={"MID": message_id, "message": "synthetic inbound"},
    )


async def test_two_pollers_create_one_inbound_bundle(monkeypatch):
    message = make_message()
    async def no_job(*args, **kwargs):
        await asyncio.sleep(0.05)
        return SimpleNamespace(needs_context=True, job=None, proposal=None, candidate_jobs=[])

    monkeypatch.setattr(kwork_inbox, "find_kwork_job_for_message", no_job)
    settings = Settings(_env_file=None)
    results = await asyncio.gather(*(
        kwork_inbox._persist_inbound_bundle(settings, message, target_chat=123)
        for _ in range(2)
    ), return_exceptions=True)
    assert not any(isinstance(value, BaseException) for value in results), results
    assert sum(value is not None for value in results) == 1
    key = kwork_inbox._message_key(message.user_id, message.raw)
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(InboundMessage.message_key == key))
        assert inbound and inbound.status == "processed" and inbound.inbox_pending_id
        assert await session.scalar(select(func.count()).select_from(Interaction).where(
            Interaction.id == inbound.interaction_id
        )) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.reference_id == inbound.inbox_pending_id
        )) == 1


async def test_concurrent_initial_seed_does_not_raise_unique_conflict(monkeypatch):
    message = make_message()
    barrier = asyncio.Barrier(2)
    original = kwork_inbox._inbound_already_recorded

    async def both_check_before_inserting(*args):
        found = await original(*args)
        await barrier.wait()
        return found

    monkeypatch.setattr(kwork_inbox, "_inbound_already_recorded", both_check_before_inserting)
    results = await asyncio.gather(
        kwork_inbox._seed_inbound_message(message), kwork_inbox._seed_inbound_message(message),
        return_exceptions=True,
    )
    assert not any(isinstance(value, BaseException) for value in results), results
    key = kwork_inbox._message_key(message.user_id, message.raw)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessage).where(
            InboundMessage.message_key == key
        )) == 1


async def test_unmatched_explicit_project_is_not_bound_to_same_username(monkeypatch):
    from app.db.models import Client, Job, TelegramInboxPending

    message = make_message()
    message.username = "buyer_" + uuid.uuid4().hex
    message.dialog = {"want_id": "999999999999999999999999999"}
    client_id, job_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Client(id=client_id, platform="kwork", external_id=f"/user/{message.username}",
                           name=message.username))
        await session.flush()
        session.add(Job(id=job_id, platform="kwork", external_id=f"kwork-{job_id.int}",
                        client_id=client_id, status="sent", title="Different job", description="x"))
        await session.commit()
    generate = AsyncMock()
    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    pending_id = await kwork_inbox._persist_inbound_bundle(
        Settings(_env_file=None), message, target_chat=123
    )
    assert pending_id
    generate.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
        interaction = await session.get(Interaction, pending.interaction_id)
        assert pending.job_id is None and pending.draft_reply == ""
        assert interaction.metadata_["needs_context"] is True
        assert interaction.metadata_["project_id"] == "999999999999999999999999999"


async def test_two_pollers_without_platform_id_create_one_bundle(monkeypatch):
    message = make_message()
    message.message_id = ''
    message.raw = {'time': 123, 'message': message.text}
    message.timestamp = 123
    async def no_job(*args, **kwargs):
        await asyncio.sleep(0.05)
        return SimpleNamespace(needs_context=True, job=None, proposal=None, candidate_jobs=[])
    monkeypatch.setattr(kwork_inbox, 'find_kwork_job_for_message', no_job)
    results = await asyncio.gather(*(kwork_inbox._persist_inbound_bundle(
        Settings(_env_file=None), message, target_chat=123) for _ in range(2)), return_exceptions=True)
    assert not any(isinstance(value, BaseException) for value in results), results
    assert sum(value is not None for value in results) == 1
    key = kwork_inbox._message_key(message.user_id, message.raw)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessage).where(
            InboundMessage.message_key == key)) == 1
