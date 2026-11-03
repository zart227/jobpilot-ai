"""Inbox generation preserves inbound on retryable chat LLM errors (C054 partial)."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import InboundMessage, Job
from app.db.session import AsyncSessionLocal
from app.services import kwork_inbox
from app.services.pipeline_processing_errors import pipeline_error_from_exception

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def test_chat_timeout_defers_inbound_without_needs_attention(monkeypatch):
    job_id = uuid.uuid4()
    message_id = uuid.uuid4().hex
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"kwork-{job_id.int}",
                title="Test job",
                description="test description",
                status="sent",
            )
        )
        await session.commit()
    msg = kwork_inbox.ClientInboxMessage(
        user_id=user_id,
        username="buyer",
        message_id=message_id,
        text="question",
        timestamp=1,
        dialog={"want_id": str(job_id.int)},
        raw={"MID": message_id, "message": "question"},
    )

    class FakeChatAgent:
        async def run(self, state):
            return {
                **pipeline_error_from_exception(TimeoutError(), stage="chat"),
                "chat_intent": "error",
                "chat_reply": "",
            }

    monkeypatch.setattr(kwork_inbox, "ChatAgent", FakeChatAgent)
    monkeypatch.setattr(kwork_inbox, "build_chat_agent_state", AsyncMock(return_value={}))
    monkeypatch.setattr(kwork_inbox, "finalize_chat_agent_state", AsyncMock(side_effect=lambda s: s))
    settings = Settings(_env_file=None, telegram_admin_chat_id="7")
    assert await kwork_inbox._persist_inbound_bundle(settings, msg, target_chat=7) is None
    async with AsyncSessionLocal() as session:
        inbound = await session.scalar(
            select(InboundMessage).where(
                InboundMessage.platform == "kwork",
                InboundMessage.message_key == kwork_inbox._message_key(user_id, msg.raw),
            )
        )
        assert inbound.status == "received"
        assert inbound.inbox_pending_id is None
        assert inbound.raw_metadata.get("generation_retry_after")
