"""Inventory must never turn missing delivery evidence into a resend decision."""
import runpy
from pathlib import Path

import pytest

_module_path = Path(__file__).resolve().parents[1] / "scripts" / "ops_pending_cards.py"
delivery_state = runpy.run_path(str(_module_path))["delivery_state"]


@pytest.mark.parametrize(("message_id", "statuses", "expected"), [
    (None, [], "delivery_unknown_no_outbox"),
    (123, [], "legacy_card_known"),
    (None, ["pending"], "queued"),
    (None, ["sending"], "delivery_uncertain"),
    (123, ["needs_attention"], "delivery_uncertain"),
    (None, ["sent"], "delivery_inconsistent"),
    (123, ["sent"], "delivery_recorded"),
    (123, ["sent", "pending"], "delivery_uncertain"),
    (123, ["sent", "sent"], "delivery_uncertain"),
    (None, ["pending", "pending"], "delivery_uncertain"),
    (123, ["pending"], "delivery_uncertain"),
    (None, ["cancelled"], "cancelled_only"),
    (None, ["unexpected"], "delivery_uncertain"),
])
def test_delivery_evidence_classification(message_id, statuses, expected):
    assert delivery_state(message_id, statuses) == expected


async def test_inventory_reports_missing_and_pending_outbox(monkeypatch, capsys):
    import os
    import uuid

    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        pytest.skip("isolated DB required")
    from app.db.models import Job, NotificationOutbox, Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal

    ids = [uuid.uuid4(), uuid.uuid4()]
    async with AsyncSessionLocal() as session:
        for pending_id in ids:
            job_id, proposal_id = uuid.uuid4(), uuid.uuid4()
            session.add(Job(id=job_id, platform="test", external_id=str(job_id),
                            title="test", description="test", status="new"))
            await session.flush()
            session.add(Proposal(id=proposal_id, job_id=job_id, content="test", status="draft", version=1))
            await session.flush()
            session.add(TelegramPending(id=pending_id, job_id=job_id, proposal_id=proposal_id,
                                        chat_id=123, status="pending"))
        await session.flush()
        session.add(NotificationOutbox(
            id=uuid.uuid4(), kind="proposal_approval", idempotency_key=f"test-{uuid.uuid4()}",
            chat_id=123, payload={}, reference_type="telegram_pending", reference_id=ids[1],
            status="pending",
        ))
        await session.commit()
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    main = runpy.run_path(str(_module_path))["main"]
    assert await main() == 0
    lines = capsys.readouterr().out.splitlines()
    first = next(line for line in lines if str(ids[0]) in line)
    second = next(line for line in lines if str(ids[1]) in line)
    assert "delivery_unknown_no_outbox" in first
    assert "'delivery_state': 'queued'" in second


async def test_inventory_requires_explicit_database(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    main = runpy.run_path(str(_module_path))["main"]
    assert await main() == 2
    assert "explicit" in capsys.readouterr().out.lower()
