"""Durable claim and fencing for inbound draft generation."""
import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import InboundMessage, Job, NotificationOutbox
from app.db.session import AsyncSessionLocal
from app.services import kwork_inbox

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def make_message():
    job_id = uuid.uuid4()
    message_id = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="kwork", external_id=f"kwork-{job_id.int}",
                        title="Test job", description="test", status="sent"))
        await session.commit()
    return kwork_inbox.ClientInboxMessage(
        user_id=int(uuid.uuid4().int % 800_000_000) + 100, username="buyer",
        message_id=message_id, text="question", timestamp=1,
        dialog={"want_id": str(job_id.int)}, raw={"MID": message_id, "message": "question"},
    )


async def get_inbound(session, msg):
    return await session.scalar(select(InboundMessage).where(
        InboundMessage.platform == "kwork",
        InboundMessage.message_key == kwork_inbox._message_key(msg.user_id, msg.raw),
    ))


async def test_concurrent_pollers_only_generate_once(monkeypatch):
    msg = await make_message()
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "question", "draft"

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    settings = Settings(_env_file=None)
    first = asyncio.create_task(kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7))
    await asyncio.wait_for(started.wait(), timeout=5)
    second = await kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7)
    release.set()
    assert await asyncio.wait_for(first, timeout=5)
    assert second is None and calls == 1


async def test_concurrent_generation_claims_yield_at_most_one_token():
    """C032: parallel claim attempts on one received row must not create overlapping leases."""
    msg = await make_message()
    await kwork_inbox._capture_inbound(msg)
    project = msg.dialog["want_id"]
    tokens = await asyncio.gather(
        kwork_inbox._claim_inbound_generation(msg, project),
        kwork_inbox._claim_inbound_generation(msg, project),
        kwork_inbox._claim_inbound_generation(msg, project),
    )
    active = [token for token in tokens if token]
    assert len(active) == 1
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        assert inbound.raw_metadata.get("generation_claim_token") == active[0]


async def test_expired_claim_is_recovered_and_stale_worker_is_fenced():
    msg = await make_message()
    await kwork_inbox._capture_inbound(msg)
    project = msg.dialog["want_id"]
    old = await kwork_inbox._claim_inbound_generation(msg, project)
    assert old
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        inbound.raw_metadata = {**inbound.raw_metadata, "generation_claim_until": "2000-01-01T00:00:00+00:00"}
        await session.commit()
    new = await kwork_inbox._claim_inbound_generation(msg, project)
    assert new and new != old
    await kwork_inbox._defer_inbound_retry(kwork_inbox._message_key(msg.user_id, msg.raw), old)
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        assert inbound.status == "received"
        assert inbound.raw_metadata["generation_claim_token"] == new
        assert inbound.raw_metadata["generation_attempts"] == 2


async def test_attempt_limit_leaves_message_for_operator(monkeypatch):
    msg = await make_message()
    await kwork_inbox._capture_inbound(msg)
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        inbound.raw_metadata = {**inbound.raw_metadata, "generation_attempts": 4}
        await session.commit()
    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", AsyncMock(side_effect=RuntimeError("offline")))
    assert await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None, telegram_admin_chat_id="7"), msg, target_chat=7) is None
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        assert inbound.status == "needs_attention"
        assert inbound.raw_metadata["generation_attempts"] == 5
        assert inbound.raw_metadata.get("generation_exhausted_notified_at")
        assert "generation_claim_token" not in inbound.raw_metadata
        assert inbound.inbox_pending_id is None
        outbox = await session.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.idempotency_key == f"inbound_generation_exhausted:{inbound.id}"
            )
        )
        assert outbox and outbox.kind == "ops_alert" and outbox.status == "pending"

async def test_operator_requeue_requires_attention_and_no_existing_review():
    msg = await make_message()
    await kwork_inbox._capture_inbound(msg)
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        inbound_id = inbound.id
        assert not await kwork_inbox.requeue_inbound_for_review(session, inbound_id)
        inbound.status = "needs_attention"
        inbound.raw_metadata = {**inbound.raw_metadata, "generation_attempts": 5}
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await kwork_inbox.requeue_inbound_for_review(session, inbound_id)
        await session.commit()
    async with AsyncSessionLocal() as session:
        inbound = await session.get(InboundMessage, inbound_id)
        assert inbound.status == "received"
        assert "generation_attempts" not in inbound.raw_metadata
        assert inbound.raw_metadata["operator_requeued_at"]
        assert not await kwork_inbox.requeue_inbound_for_review(session, inbound_id)

@pytest.mark.parametrize("capture_mode,expected_status", [("received", "received"), ("seeded", "seeded")])
async def test_first_dialog_is_captured_before_later_network_failure(capture_mode, expected_status):
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    message_id = uuid.uuid4().hex
    api = type("FakeApi", (), {})()
    api.get_dialogs = AsyncMock(return_value=[
        {"username": "first", "user_id": user_id, "unread": True},
        {"username": "second", "user_id": user_id + 1, "unread": True},
    ])
    api.get_messages = AsyncMock(side_effect=[
        [{"MID": message_id, "message": "hello", "from_id": user_id, "time": 1}],
        RuntimeError("later network failure"),
    ])
    with pytest.raises(RuntimeError, match="later network failure"):
        await kwork_inbox._collect_new_messages(
            api, kwork_inbox.InboxState([]), my_username="seller", my_user_id=1,
            capture_mode=capture_mode,
        )
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == f"{user_id}:{message_id}"
        ))
        assert inbound and inbound.status == expected_status


async def test_each_message_in_dialog_captured_before_mid_dialog_crash(monkeypatch):
    """First message must be committed before a crash while capturing the second."""
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    first_id = uuid.uuid4().hex
    second_id = uuid.uuid4().hex
    api = type("FakeApi", (), {})()
    api.get_dialogs = AsyncMock(return_value=[
        {"username": "buyer", "user_id": user_id, "unread": True},
    ])
    api.get_messages = AsyncMock(return_value=[
        {"MID": first_id, "message": "first", "from_id": user_id, "time": 1},
        {"MID": second_id, "message": "second", "from_id": user_id, "time": 2},
    ])
    original_capture = kwork_inbox._capture_inbound

    async def flaky_capture(msg):
        if msg.raw.get("MID") == second_id:
            raise RuntimeError("mid-dialog crash")
        await original_capture(msg)

    monkeypatch.setattr(kwork_inbox, "_capture_inbound", flaky_capture)
    with pytest.raises(RuntimeError, match="mid-dialog crash"):
        await kwork_inbox._collect_new_messages(
            api, kwork_inbox.InboxState([]), my_username="seller", my_user_id=1,
            capture_mode="received",
        )
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == f"{user_id}:{first_id}"
        ))
        assert inbound and inbound.status == "received"

async def test_eager_capture_persists_when_validation_loop_crashes(monkeypatch):
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    message_id = uuid.uuid4().hex
    api = type("FakeApi", (), {})()
    api.get_dialogs = AsyncMock(return_value=[
        {"username": "buyer", "user_id": user_id, "unread": True},
    ])
    api.get_messages = AsyncMock(return_value=[
        {"MID": message_id, "message": "hello", "from_id": user_id, "time": 1},
    ])

    async def crash_on_already_recorded(session, platform, key):
        raise RuntimeError("crash during dedup scan")

    monkeypatch.setattr(kwork_inbox, "_inbound_already_recorded", crash_on_already_recorded)
    with pytest.raises(RuntimeError, match="crash during dedup scan"):
        await kwork_inbox._collect_new_messages(
            api, kwork_inbox.InboxState([]), my_username="seller", my_user_id=1,
            capture_mode="received",
        )
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == f"{user_id}:{message_id}"
        ))
        assert inbound and inbound.status == "received"


async def test_kwork_api_error_on_get_messages_skips_dialog_without_aborting_poll():
    """C027 outage drill: Kwork API failure on one dialog must not drop other dialogs."""
    from app.services.kwork_api import KworkApiError

    user_ok = int(uuid.uuid4().int % 800_000_000) + 100
    user_fail = user_ok + 1
    ok_id = uuid.uuid4().hex
    api = type("FakeApi", (), {})()
    api.get_dialogs = AsyncMock(return_value=[
        {"username": "fail", "user_id": user_fail, "unread": True},
        {"username": "ok", "user_id": user_ok, "unread": True},
    ])

    async def messages(username):
        if username == "fail":
            raise KworkApiError("upstream timeout")
        return [{"MID": ok_id, "message": "hello", "from_id": user_ok, "time": 1}]

    api.get_messages = messages
    collected = await kwork_inbox._collect_new_messages(
        api, kwork_inbox.InboxState([]), my_username="seller", my_user_id=999_999,
        capture_mode="received",
    )
    assert collected == []
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(select(InboundMessage).where(
            InboundMessage.platform == "kwork", InboundMessage.message_key == f"{user_ok}:{ok_id}"
        ))
        assert inbound and inbound.status == "received"


async def test_active_claims_do_not_starve_later_received_messages(monkeypatch):
    from datetime import UTC, datetime

    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        for index in range(50):
            session.add(InboundMessage(
                platform="kwork", message_key=f"{index}:{suffix}", thread_key=str(index),
                kwork_user_id=index, kwork_username="buyer", body="test", status="received",
                received_at=datetime(2020, 1, 1, tzinfo=UTC),
                raw_metadata={"generation_claim_until": "2999-01-01T00:00:00+00:00"},
            ))
        user_id = int(uuid.uuid4().int % 800_000_000) + 100
        key = f"{user_id}:{suffix}"
        session.add(InboundMessage(
            platform="kwork", message_key=key, thread_key=str(user_id),
            kwork_user_id=user_id, kwork_username="buyer", body="test", status="received",
            received_at=datetime(2020, 1, 2, tzinfo=UTC), raw_metadata={"timestamp": 1},
        ))
        await session.commit()
    process = AsyncMock(return_value=0)
    monkeypatch.setattr(kwork_inbox, "_process_and_notify", process)
    await kwork_inbox._retry_received_inbound(Settings(_env_file=None, telegram_admin_chat_id="7"))
    messages = process.await_args.args[1]
    assert any(kwork_inbox._message_key(msg.user_id, msg.raw) == key for msg in messages)
    assert not any(msg.message_id == suffix and msg.user_id < 50 for msg in messages)

async def test_heartbeat_prevents_expiry_during_long_generation(monkeypatch):
    msg = await make_message()
    monkeypatch.setattr(kwork_inbox, "INBOUND_GENERATION_LEASE_SECONDS", 3)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "question", "draft"

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    settings = Settings(_env_file=None)
    first = asyncio.create_task(kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(4)
        assert await kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7) is None
        assert calls == 1
        release.set()
        assert await asyncio.wait_for(first, timeout=5)
    finally:
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)


async def test_lost_heartbeat_cancels_generation_and_does_not_create_review(monkeypatch):
    msg = await make_message()
    monkeypatch.setattr(kwork_inbox, "INBOUND_GENERATION_LEASE_SECONDS", 0.3)
    monkeypatch.setattr(kwork_inbox, "_renew_inbound_claim", AsyncMock(side_effect=[True, False]))
    cancelled = asyncio.Event()

    async def generate(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    result = await asyncio.wait_for(
        kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), msg, target_chat=7), timeout=5
    )
    assert result is None and cancelled.is_set()
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        assert inbound.status == "received" and inbound.inbox_pending_id is None


async def test_expired_or_replaced_token_cannot_extend_claim():
    msg = await make_message()
    await kwork_inbox._capture_inbound(msg)
    key = kwork_inbox._message_key(msg.user_id, msg.raw)
    token = await kwork_inbox._claim_inbound_generation(msg, msg.dialog["want_id"])
    assert token
    assert not await kwork_inbox._renew_inbound_claim(key, str(uuid.uuid4()))
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        inbound.raw_metadata = {**inbound.raw_metadata, "generation_claim_until": "2000-01-01T00:00:00+00:00"}
        await session.commit()
    assert not await kwork_inbox._renew_inbound_claim(key, token)

async def test_lease_starts_after_database_row_lock_is_acquired(monkeypatch):
    msg = await make_message()
    await kwork_inbox._capture_inbound(msg)
    monkeypatch.setattr(kwork_inbox, "INBOUND_GENERATION_LEASE_SECONDS", 0.5)
    async with AsyncSessionLocal() as session:
        await session.scalar(select(InboundMessage).where(
            InboundMessage.message_key == kwork_inbox._message_key(msg.user_id, msg.raw)
        ).with_for_update())
        task = asyncio.create_task(kwork_inbox._claim_inbound_generation(msg, msg.dialog["want_id"]))
        await asyncio.sleep(0.7)
        assert not task.done()
        await session.commit()
    token = await asyncio.wait_for(task, timeout=5)
    assert token
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        assert kwork_inbox._claim_is_current(inbound, token)


async def test_expired_claim_during_lookup_does_not_start_generation(monkeypatch):
    msg = await make_message()
    monkeypatch.setattr(kwork_inbox, "INBOUND_GENERATION_LEASE_SECONDS", 0.5)
    original = kwork_inbox.find_kwork_job_for_message

    async def slow_lookup(*args, **kwargs):
        match = await original(*args, **kwargs)
        await asyncio.sleep(0.7)
        return match

    generate = AsyncMock(return_value=("question", "draft"))
    monkeypatch.setattr(kwork_inbox, "find_kwork_job_for_message", slow_lookup)
    monkeypatch.setattr(kwork_inbox, "_generate_inbox_reply", generate)
    assert await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), msg, target_chat=7) is None
    generate.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        inbound = await get_inbound(session, msg)
        assert inbound.status == "received" and inbound.inbox_pending_id is None
