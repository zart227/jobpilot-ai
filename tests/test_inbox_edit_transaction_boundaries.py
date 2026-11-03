"""Editing an inbox draft releases DB sessions before fetching Kwork history."""
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Router

from app.db.models import Job, TelegramInboxPending
from app.db.session import AsyncSessionLocal
from app.services.inbox_approval import capture_inbox_presentation
from app.telegram import inbox_handlers

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required'
)


async def seed():
    job_id, pending_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='Title', description='Description'))
        await session.flush()
        pending = TelegramInboxPending(id=pending_id, job_id=job_id, kwork_user_id=7,
            kwork_username='buyer', client_message='Question', draft_reply='Reply',
            status='pending', chat_id=100, message_id=55)
        session.add(pending)
        await session.flush()
        await capture_inbox_presentation(session, pending)
        await session.commit()
    return pending_id


def edit_handler():
    router = Router()
    inbox_handlers.register_inbox_handlers(router)
    return next(h.callback for h in router.callback_query.handlers if h.callback.__name__ == 'handle_inbox_edit')


@pytest.mark.parametrize('change_during_fetch', [False, True])
async def test_edit_fetch_and_summary_release_session_and_reject_stale(monkeypatch, change_during_fetch):
    pending_id = await seed()
    active = {'count': 0}
    class Tracked:
        async def __aenter__(self):
            self.session = AsyncSessionLocal()
            await self.session.__aenter__()
            active['count'] += 1
            return self.session
        async def __aexit__(self, *args):
            try:
                return await self.session.__aexit__(*args)
            finally:
                active['count'] -= 1
    monkeypatch.setattr(inbox_handlers, 'AsyncSessionLocal', Tracked)
    async def actor():
        assert active['count'] == 0
        return 'seller', 1
    async def fetch(_username):
        assert active['count'] == 0
        if change_during_fetch:
            async with AsyncSessionLocal() as session:
                pending = await session.get(TelegramInboxPending, pending_id)
                pending.draft_reply = 'changed while fetching'
                await session.commit()
        return []
    async def finalize(state):
        assert active['count'] == 0
        return state
    monkeypatch.setattr(inbox_handlers, '_get_kwork_actor', actor)
    monkeypatch.setattr(inbox_handlers, '_fetch_kwork_thread', fetch)
    monkeypatch.setattr(inbox_handlers, 'finalize_chat_agent_state', finalize)
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    message = SimpleNamespace(chat=SimpleNamespace(id=100), message_id=55, answer=AsyncMock())
    callback = SimpleNamespace(data=f'inbox_edit:{pending_id}', message=message, answer=AsyncMock())
    await edit_handler()(callback, state)
    if change_during_fetch:
        state.set_state.assert_not_awaited()
        callback.answer.assert_awaited_once_with('Карточка устарела — запросите новую', show_alert=True)
    else:
        state.set_state.assert_awaited_once()
        assert state.update_data.await_args.kwargs['draft_reply'] == 'Reply'
