"""C012: stale/wrong Telegram cards and proposal versions must not authorize actions."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, User

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)

ROOT = Path(__file__).resolve().parents[1]

CALLBACK_FSM_EVIDENCE = (
    ("proposal_snapshot_version", "tests/test_approval_snapshot.py"),
    ("proposal_outbox_atomicity", "tests/test_proposal_outbox_atomicity.py"),
    ("inbox_approval_enforcement", "tests/test_inbox_approval_enforcement.py"),
    ("inbox_edit_preview_binding", "tests/test_edit_preview_binding.py"),
    ("inbox_edit_transaction_boundaries", "tests/test_inbox_edit_transaction_boundaries.py"),
    ("full_text_review_controls", "tests/test_full_text_review.py"),
    ("draft_mutation_guards", "tests/test_draft_mutation_guards.py"),
    ("c012_proposal_handler_stale", "tests/test_c012_callback_fsm_acceptance.py"),
)


def test_c012_callback_fsm_evidence_catalog_covers_core_modules():
    assert len(CALLBACK_FSM_EVIDENCE) >= 8
    for _name, path in CALLBACK_FSM_EVIDENCE:
        assert (ROOT / path).is_file()


@pytest.fixture
def review_telegram(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "7000000000:AAFakeTokenForTests")
    from aiogram.fsm.storage.memory import MemoryStorage

    monkeypatch.setattr("app.telegram.bot.RedisStorage", lambda **_kw: MemoryStorage())
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_proposal_pending_card():
    from conftest import seed_test_kwork_eligibility

    from app.db.models import Job, Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import capture_proposal_card

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c012-{job_id.hex[:10]}",
                title="C012 card",
                description="Desc",
                url="https://kwork.ru/projects/1/view",
                budget_min=1000,
                budget_max=2000,
                status="pending_approval",
            )
        )
        await session.flush()
        await seed_test_kwork_eligibility(session, job_id)
        await session.flush()
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Immutable review text",
                status="pending_approval",
                version=1,
            )
        )
        await session.flush()
        await capture_proposal_card(session, pending_id, await session.get(Job, job_id), await session.get(Proposal, proposal_id))
        session.add(
            TelegramPending(
                id=pending_id,
                job_id=job_id,
                proposal_id=proposal_id,
                chat_id=100,
                message_id=5001,
                status="pending",
            )
        )
        await session.commit()
    return pending_id, job_id, proposal_id


@pytest.mark.asyncio
async def test_c012_proposal_approve_rejects_wrong_telegram_message_id(review_telegram):
    from app.db.models import Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.telegram.bot import create_dispatcher

    pending_id, _, proposal_id = await _seed_proposal_pending_card()
    dp = create_dispatcher()
    approve = next(
        h.callback for h in dp.callback_query.handlers if h.callback.__name__ == "handle_approve"
    )
    operator = User(id=100, is_bot=False, first_name="Op")
    callback = SimpleNamespace(
        from_user=operator,
        data=f"approve:{pending_id}",
        message=SimpleNamespace(
            chat=Chat(id=100, type="private"),
            message_id=5002,
            answer=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    await approve(callback)
    callback.answer.assert_awaited()
    assert "устарела" in callback.answer.await_args.args[0].lower()
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        proposal = await session.get(Proposal, proposal_id)
        assert pending is not None and pending.status == "pending"
        assert proposal is not None and proposal.status == "pending_approval"


@pytest.mark.asyncio
async def test_c012_proposal_approve_rejects_mutated_proposal_text(review_telegram):
    from app.db.models import Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.telegram.bot import create_dispatcher

    pending_id, _, proposal_id = await _seed_proposal_pending_card()
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, proposal_id)
        proposal.content = "Mutated after card was shown"
        await session.commit()

    dp = create_dispatcher()
    approve = next(
        h.callback for h in dp.callback_query.handlers if h.callback.__name__ == "handle_approve"
    )
    operator = User(id=100, is_bot=False, first_name="Op")
    callback = SimpleNamespace(
        from_user=operator,
        data=f"approve:{pending_id}",
        message=SimpleNamespace(
            chat=Chat(id=100, type="private"),
            message_id=5001,
            answer=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    await approve(callback)
    callback.answer.assert_awaited()
    assert "устарела" in callback.answer.await_args.args[0].lower()
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        assert pending is not None and pending.status == "pending"


@pytest.mark.asyncio
async def test_c012_proposal_skip_rejects_stale_message_id(review_telegram):
    from app.db.models import TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.telegram.bot import create_dispatcher

    pending_id, _, _ = await _seed_proposal_pending_card()
    dp = create_dispatcher()
    skip = next(h.callback for h in dp.callback_query.handlers if h.callback.__name__ == "handle_skip")
    callback = SimpleNamespace(
        from_user=User(id=100, is_bot=False, first_name="Op"),
        data=f"skip:{pending_id}",
        message=SimpleNamespace(
            chat=Chat(id=100, type="private"),
            message_id=9999,
            answer=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    await skip(callback)
    assert "устарела" in callback.answer.await_args.args[0].lower()
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        assert pending is not None and pending.status == "pending"
