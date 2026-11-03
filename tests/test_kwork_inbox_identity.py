"""Missing platform IDs require stable keys and conservative legacy handling."""
import os
import subprocess
import sys
import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.kwork_inbox import InboxState, _collect_new_messages, _message_key


def test_fallback_key_stable_between_processes_and_uses_full_text():
    code = 'from app.services.kwork_inbox import _message_key; print(_message_key(42,{"time":123,"message":"hello"}))'
    keys = [subprocess.check_output([sys.executable, '-c', code], text=True,
                                   env={**os.environ, 'PYTHONHASHSEED': seed}).strip()
            for seed in ('1', '2')]
    assert keys[0] == keys[1] == _message_key(42, {'time': 123, 'message': 'hello'})
    assert _message_key(42, {'time': 123, 'message': 'x' * 80 + 'a'}) != _message_key(
        42, {'time': 123, 'message': 'x' * 80 + 'b'})


def test_missing_timestamp_is_not_fabricated():
    with pytest.raises(ValueError):
        _message_key(42, {'message': 'hello'})


@pytest.mark.skipif(os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required')
@pytest.mark.parametrize('mode', ['legacy_db', 'legacy_json', 'new', 'duplicate', 'missing_time'])
async def test_fallback_compatibility_and_ambiguous_data(mode):
    from app.db.models import InboundMessage
    from app.db.session import AsyncSessionLocal

    user_id = int(uuid.uuid4().int % (10**8)) + 1
    message = {'from_id': user_id, 'message': 'hello', 'time': 123}
    state = InboxState(seen_keys=[])
    if mode == 'legacy_db':
        async with AsyncSessionLocal() as session:
            session.add(InboundMessage(platform='kwork', message_key=f'{user_id}:123:-987',
                thread_key=str(user_id), kwork_user_id=user_id, kwork_username='buyer',
                body='hello', raw_metadata={'timestamp': 123}, status='processed'))
            await session.commit()
    if mode == 'legacy_json':
        state.seen_keys = [f'{user_id}:123:-987']
    if mode == 'missing_time':
        message.pop('time')
    messages = [message, dict(message)] if mode == 'duplicate' else [message]
    api = type('Api', (), {})()
    api.get_dialogs = AsyncMock(return_value=[{'username': 'buyer', 'user_id': user_id, 'unread': True}])
    api.get_messages = AsyncMock(return_value=messages)
    result = await _collect_new_messages(api, state, my_username='seller', my_user_id=999999999)
    if mode == 'new':
        assert len(result) == 1 and not state.identity_ambiguities
    else:
        assert result == []
        assert bool(state.identity_ambiguities) == (mode != 'legacy_db')
    if mode == 'legacy_db':
        assert state.is_seen(_message_key(user_id, message))


@pytest.mark.skipif(os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required')
async def test_legacy_database_record_blocks_direct_reprocessing_without_raw_timestamp(monkeypatch):
    from app.config import Settings
    from app.db.models import InboundMessage
    from app.db.session import AsyncSessionLocal
    from app.services import kwork_inbox

    user_id = int(uuid.uuid4().int % (10**8)) + 1
    async with AsyncSessionLocal() as session:
        session.add(InboundMessage(platform='kwork', message_key=f'{user_id}:123:-987',
            thread_key=str(user_id), kwork_user_id=user_id, kwork_username='buyer',
            body='hello', raw_metadata={}, status='processed'))
        await session.commit()
    msg = kwork_inbox.ClientInboxMessage(user_id=user_id, username='buyer', message_id='',
        text='hello', timestamp=123, dialog={}, raw={'time': 123, 'message': 'hello'})
    match = AsyncMock()
    monkeypatch.setattr(kwork_inbox, 'find_kwork_job_for_message', match)
    assert await kwork_inbox._persist_inbound_bundle(Settings(_env_file=None), msg, target_chat=100) is None
    match.assert_not_awaited()
