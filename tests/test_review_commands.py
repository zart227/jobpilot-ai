import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Router

from app.services.review_reissue import ReviewReissueResult
from app.telegram import review_handlers


def message(text):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=100),
        from_user=SimpleNamespace(id=5),
        reply_to_message=None,
        answer=AsyncMock(),
    )


def handler():
    router = Router()
    review_handlers.register_review_handlers(router)
    return router.message.handlers[0].callback


@pytest.mark.asyncio
async def test_unauthorized_command_does_not_touch_review_service(monkeypatch):
    monkeypatch.setattr(review_handlers, "is_admin_chat_message", lambda _: False)
    service = AsyncMock()
    monkeypatch.setattr(review_handlers, "reissue_review", service)
    await handler()(message("/rereview proposal " + str(uuid.uuid4())))
    service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "/rereview",
        "/rereview inbox invalid",
        "/rereview badkind abc",
        "/rereview proposal",
        "/rereview inbox one two",
    ],
)
async def test_invalid_command_never_queues_review(text, monkeypatch):
    monkeypatch.setattr(review_handlers, "is_admin_chat_message", lambda _: True)
    service = AsyncMock()
    monkeypatch.setattr(review_handlers, "reissue_review", service)
    request = message(text)
    await handler()(request)
    service.assert_not_awaited()
    assert "/rereview" in request.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_explicit_id_queues_review_but_does_not_claim_delivery(monkeypatch):
    monkeypatch.setattr(review_handlers, "is_admin_chat_message", lambda _: True)
    pending_id, new_id = uuid.uuid4(), uuid.uuid4()
    service = AsyncMock(return_value=ReviewReissueResult("queued", new_id, uuid.uuid4()))
    monkeypatch.setattr(review_handlers, "reissue_review", service)
    request = message(f"/rereview inbox {pending_id}")
    await handler()(request)
    service.assert_awaited_once_with(
        "inbox", pending_id, chat_id=100, user_id=5, source_message_id=None
    )
    assert "Нужно новое одобрение" in request.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("same_source", [False, True])
async def test_reissue_only_clears_fsm_for_replaced_card(monkeypatch, same_source):
    monkeypatch.setattr(review_handlers, "is_admin_chat_message", lambda _: True)
    pending_id = uuid.uuid4()
    service = AsyncMock(return_value=ReviewReissueResult("queued", uuid.uuid4(), uuid.uuid4()))
    monkeypatch.setattr(review_handlers, "reissue_review", service)
    state = SimpleNamespace(
        get_data=AsyncMock(
            return_value={"pending_id": str(pending_id if same_source else uuid.uuid4())}
        ),
        clear=AsyncMock(),
    )
    await handler()(message(f"/rereview proposal {pending_id}"), state)
    assert state.clear.await_count == (1 if same_source else 0)


@pytest.mark.asyncio
async def test_repeated_command_does_not_claim_new_approval_or_delivery(monkeypatch):
    monkeypatch.setattr(review_handlers, "is_admin_chat_message", lambda _: True)
    pending_id = uuid.uuid4()
    service = AsyncMock(
        return_value=ReviewReissueResult("already_queued", uuid.uuid4(), uuid.uuid4())
    )
    monkeypatch.setattr(review_handlers, "reissue_review", service)
    request = message(f"/rereview proposal {pending_id}")
    await handler()(request)
    assert "уже была поставлена" in request.answer.await_args.args[0]
    assert "Нужно новое одобрение" not in request.answer.await_args.args[0]
