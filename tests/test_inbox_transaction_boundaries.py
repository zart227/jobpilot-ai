"""Inbound lookups must release database sessions before external work."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import InboundMessage, Job
from app.db.session import AsyncSessionLocal
from app.services import kwork_inbox

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def job_and_message():
    jid = uuid.uuid4()
    project_id = str(jid.int)
    message_id = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        session.add(Job(id=jid, platform="kwork", external_id=f"kwork-{project_id}",
                        title="Original project", description="d", status="sent"))
        await session.commit()
    msg = kwork_inbox.ClientInboxMessage(
        user_id=42, username="synthetic", message_id=message_id, text="question", timestamp=1,
        dialog={"want_id": project_id}, raw={"MID": message_id, "message": "question"},
    )
    return jid, msg


@pytest.fixture
def track_inbox_sessions(monkeypatch):
    active = {"count": 0}

    class Tracked:
        async def __aenter__(self):
            self.session = AsyncSessionLocal()
            await self.session.__aenter__()
            active["count"] += 1
            return self.session

        async def __aexit__(self, *args):
            try:
                return await self.session.__aexit__(*args)
            finally:
                active["count"] -= 1

    monkeypatch.setattr(kwork_inbox, "AsyncSessionLocal", Tracked)
    return active


async def test_generate_has_no_caller_db_session(track_inbox_sessions, monkeypatch):
    _, message = await job_and_message()
    observed = []

    async def generate(*args, **kwargs):
        observed.append(track_inbox_sessions["count"])
        return "question", "draft"

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    pending_id = await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), message, target_chat=7)
    assert pending_id and observed == [0]


async def test_message_fetch_has_no_open_inbox_db_session(track_inbox_sessions):
    observed = []
    api = type("FakeAPI", (), {})()
    api.get_dialogs = AsyncMock(return_value=[{"username": "buyer", "user_id": 42, "unread": True}])

    async def get_messages(_username):
        observed.append(track_inbox_sessions["count"])
        return [{"MID": uuid.uuid4().hex, "message": "question", "from_id": 42, "time": 1}]

    api.get_messages = get_messages
    result = await kwork_inbox._collect_new_messages(
        api, kwork_inbox.InboxState([]), my_username="seller", my_user_id=1
    )
    assert len(result) == 1 and observed == [0]


async def test_changed_job_during_generation_discards_result(monkeypatch):
    jid, message = await job_and_message()

    async def generate(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, jid)
            job.title = "Changed while generating"
            await session.commit()
        return "question", "stale draft"

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    assert await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), message, target_chat=7) is None
    async with AsyncSessionLocal() as session:
        key = kwork_inbox._message_key(message.user_id, message.raw)
        inbound = await session.scalar(select(InboundMessage).where(InboundMessage.message_key == key))
        assert inbound is not None and inbound.status == "received"


async def test_summary_cache_update_does_not_discard_unchanged_context(monkeypatch):
    jid, message = await job_and_message()

    async def generate(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, jid)
            job.raw_data = {**(job.raw_data or {}), "inbox_conversation_summary": {"text": "summary"}}
            await session.commit()
        return "question", "draft"

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    assert await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), message, target_chat=7)


async def test_detached_job_works_with_real_generation_wrapper(monkeypatch):
    from app.db.models import TelegramInboxPending

    _, message = await job_and_message()
    monkeypatch.setattr(kwork_inbox, "_fetch_kwork_thread", AsyncMock(return_value=[]))
    build = AsyncMock(return_value={"client_message": "question"})
    monkeypatch.setattr(kwork_inbox, "build_chat_agent_state", build)
    agent = type("FakeChat", (), {})()
    agent.run = AsyncMock(return_value={"chat_intent": "question", "chat_reply": "draft"})
    monkeypatch.setattr(kwork_inbox, "ChatAgent", lambda: agent)
    pending_id = await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), message, target_chat=7)
    assert pending_id
    build.assert_awaited_once()
    agent.run.assert_awaited_once()
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
        assert pending.draft_reply == "draft"


async def test_changed_proposal_during_generation_discards_result(monkeypatch):
    from app.db.models import Proposal

    jid, message = await job_and_message()
    proposal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Proposal(id=proposal_id, job_id=jid, content="old", version=1, status="sent"))
        await session.commit()

    async def generate(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            proposal = await session.get(Proposal, proposal_id)
            proposal.content = "changed"
            proposal.version = 2
            await session.commit()
        return "question", "stale draft"

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    assert await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), message, target_chat=7) is None

async def test_failed_generation_is_captured_and_retried_after_restart(monkeypatch):
    from app.db.models import TelegramInboxPending

    _, message = await job_and_message()
    settings = Settings(_env_file=None, telegram_admin_chat_id="7")

    async def fail(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            inbound = await session.scalar(select(InboundMessage).where(
                InboundMessage.message_key == kwork_inbox._message_key(message.user_id, message.raw)
            ))
            assert inbound is not None and inbound.status == "received"
        raise RuntimeError("test LLM outage")

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", fail)
    assert await kwork_inbox._persist_inbound_bundle(settings, message, target_chat=7) is None
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.message_key == kwork_inbox._message_key(message.user_id, message.raw)
        ))
        assert inbound.status == "received" and inbound.inbox_pending_id is None
        assert inbound.raw_metadata["generation_attempts"] == 1
        assert inbound.raw_metadata["generation_retry_after"]
        inbound.raw_metadata = {**inbound.raw_metadata, "generation_retry_after": "2000-01-01T00:00:00+00:00"}
        await session.commit()

    generate = AsyncMock(return_value=("question", "recovered draft"))
    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    deliver = AsyncMock(return_value={"delivered": 1})
    monkeypatch.setattr(kwork_inbox, "deliver_notification_outbox", deliver)
    await kwork_inbox._retry_received_inbound(settings, my_username="seller", my_user_id=1)
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.message_key == kwork_inbox._message_key(message.user_id, message.raw)
        ))
        assert inbound.status == "processed"
        pending = await session.get(TelegramInboxPending, inbound.inbox_pending_id)
        assert pending.draft_reply == "recovered draft"
    assert generate.await_count >= 1


async def test_empty_agent_result_is_an_error(monkeypatch):
    _, message = await job_and_message()
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.external_id == "kwork-" + message.dialog["want_id"]))
    monkeypatch.setattr(kwork_inbox, "build_chat_agent_state", AsyncMock(return_value={}))
    monkeypatch.setattr(kwork_inbox, "finalize_chat_agent_state", AsyncMock(return_value={}))
    agent = type("FakeChat", (), {})()
    agent.run = AsyncMock(return_value={"pipeline_processing_error": True, "chat_reply": ""})
    monkeypatch.setattr(kwork_inbox, "ChatAgent", lambda: agent)
    with pytest.raises(RuntimeError, match="generation failed"):
        await kwork_inbox._generate_inbox_reply(job, None, "question")

async def test_batch_is_captured_before_first_generation(monkeypatch):
    _, first = await job_and_message()
    _, second = await job_and_message()
    settings = Settings(_env_file=None, telegram_admin_chat_id="7")

    async def crash(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            for message in (first, second):
                inbound = await session.scalar(select(InboundMessage).where(
                    InboundMessage.message_key == kwork_inbox._message_key(message.user_id, message.raw)
                ))
                assert inbound is not None and inbound.status == "received"
        raise RuntimeError("simulated process failure")

    monkeypatch.setattr(kwork_inbox, "_persist_inbound_bundle", crash)
    with pytest.raises(RuntimeError, match="simulated process failure"):
        await kwork_inbox._process_and_notify(settings, [first, second])
