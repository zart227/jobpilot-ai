"""C102: mock inbound → thread context → edit FSM → approval → confirmed inbox reply."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Router
from aiogram.types import Chat, User
from sqlalchemy import func, select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


@pytest.fixture
def platform_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _inbox_handlers():
    from app.telegram.inbox_handlers import register_inbox_handlers

    router = Router()
    register_inbox_handlers(router)
    by_name = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
    msg_handlers = {h.callback.__name__: h.callback for h in router.message.handlers}
    return by_name, msg_handlers


@pytest.mark.asyncio
async def test_c102_inbound_edit_telegram_fsm_approve_confirmed_reply(
    platform_dry_run,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.config import Settings
    from app.db.models import (
        InboundMessage,
        Interaction,
        Job,
        Proposal,
        SendAttempt,
        TelegramInboxPending,
    )
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.kwork_inbox import ClientInboxMessage, _persist_inbound_bundle
    from app.telegram import inbox_handlers

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    external_id = f"kwork-c102-{job_id.hex[:12]}"
    project_num = str(int(uuid.uuid4().int % 900_000) + 100_000)
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="C102 inbox job",
                description="Job description for thread context.",
                url=f"https://kwork.ru/projects/{project_num}/view",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Confirmed proposal snapshot text.",
                status="sent",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c102-proposal:{proposal_id}",
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content="Confirmed proposal snapshot text.",
                snapshot_version=1,
                status=ATTEMPT_CONFIRMED_SENT,
                platform_response={"dry_run": True},
            )
        )
        await session.commit()

    inbox_user_id = int(uuid.uuid4().int % 800_000_000) + 100
    message_id = uuid.uuid4().hex[:12]
    msg = ClientInboxMessage(
        user_id=inbox_user_id,
        username="buyer_c102",
        message_id=message_id,
        text="Уточните срок выполнения?",
        timestamp=1,
        dialog={"want_id": project_num},
        raw={"MID": message_id, "message": "Уточните срок выполнения?", "from_username": "buyer_c102"},
    )
    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)

    conversation_context = (
        "Клиент: Уточните срок выполнения?\n"
        "Исполнитель: Мы отправили отклик с подтверждённым текстом."
    )

    with patch(
        "app.services.kwork_inbox._generate_inbox_reply",
        return_value=("question", "Срок 3 рабочих дня."),
    ):
        pending_id_str = await _persist_inbound_bundle(
            settings,
            msg,
            target_chat=100,
            my_username="seller",
            my_user_id=1,
        )
    assert pending_id_str is not None
    pending_uuid = uuid.UUID(pending_id_str)
    card_message_id = 8801

    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, pending_uuid)
        assert pending is not None
        pending.message_id = card_message_id
        pending.chat_id = 100
        await session.commit()

    monkeypatch.setattr(inbox_handlers, "_get_kwork_actor", AsyncMock(return_value=("seller", 1)))
    monkeypatch.setattr(inbox_handlers, "_fetch_kwork_thread", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        inbox_handlers,
        "finalize_chat_agent_state",
        AsyncMock(
            return_value={
                "conversation_context": conversation_context,
                "proposal_content": "Confirmed proposal snapshot text.",
                "proposal_id": str(proposal_id),
            }
        ),
    )

    callbacks, messages = _inbox_handlers()
    edit_callback = SimpleNamespace(
        data=f"inbox_edit:{pending_uuid}",
        message=SimpleNamespace(
            chat=Chat(id=100, type="private"),
            message_id=card_message_id,
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    fsm_state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    await callbacks["handle_inbox_edit"](edit_callback, fsm_state)
    fsm_state.set_state.assert_awaited()
    assert "conversation_context" in fsm_state.update_data.await_args.kwargs

    class FakeEditor:
        async def run(self, **_kwargs):
            assert _kwargs.get("conversation_context") == conversation_context
            return "Срок 3 рабочих дня, старт завтра."

    monkeypatch.setattr(inbox_handlers, "EditReplyAgent", FakeEditor)

    state_data = {
        "pending_id": str(pending_uuid),
        "draft_reply": "Срок 3 рабочих дня.",
        "original_reply": "Срок 3 рабочих дня.",
        "original_chat_id": 100,
        "original_message_id": card_message_id,
        "client_message": msg.text,
        "username": msg.username,
        "job_title": "C102 inbox job",
        "job_description": "Job description for thread context.",
        "proposal_content": "Confirmed proposal snapshot text.",
        "conversation_context": conversation_context,
        "edit_steps": [],
    }
    fsm_ctx = SimpleNamespace(
        get_data=AsyncMock(return_value=state_data),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
        clear=AsyncMock(),
    )

    operator = User(id=100, is_bot=False, first_name="Operator")
    instruction_msg = SimpleNamespace(
        message_id=8802,
        chat=Chat(id=100, type="private"),
        from_user=operator,
        text="Добавь, что старт завтра.",
        answer=AsyncMock(side_effect=lambda *a, **k: SimpleNamespace(message_id=8803)),
    )

    with patch("app.telegram.inbox_handlers.send_full_review", new=AsyncMock(return_value=instruction_msg)):
        await messages["receive_inbox_edit_instruction"](instruction_msg, fsm_ctx)

    preview_callback = SimpleNamespace(
        data=f"inbox_edit_send:{pending_uuid}",
        from_user=operator,
        message=SimpleNamespace(
            chat=Chat(id=100, type="private"),
            message_id=8803,
            edit_reply_markup=AsyncMock(return_value=True),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    from app.telegram.access import edit_preview_binding

    state_data.update(
        {
            "draft_reply": "Срок 3 рабочих дня, старт завтра.",
            **edit_preview_binding(preview_callback.message, "Срок 3 рабочих дня, старт завтра."),
        }
    )
    fsm_ctx.get_data = AsyncMock(return_value=state_data)

    await callbacks["handle_inbox_edit_send"](preview_callback, fsm_ctx)

    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, pending_uuid)
        assert pending is not None
        assert pending.status == "sent"
        assert pending.draft_reply == "Срок 3 рабочих дня, старт завтра."
        outbound = await session.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(
                Interaction.direction == "outbound",
                Interaction.job_id == job_id,
            )
        )
        assert int(outbound or 0) == 1
        attempts = await session.scalar(
            select(func.count())
            .select_from(SendAttempt)
            .where(
                SendAttempt.job_id == job_id,
                SendAttempt.kind == "inbox_reply",
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert int(attempts or 0) == 1
        inbound = await session.scalar(
            select(InboundMessage).where(InboundMessage.message_key == f"{inbox_user_id}:{message_id}")
        )
        assert inbound is not None
