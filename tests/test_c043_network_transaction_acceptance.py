"""C043: external I/O must not run under long-lived DB transactions."""

from __future__ import annotations

import ast
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]

C043_INTEGRATION_EVIDENCE = [
    "tests/test_inbox_transaction_boundaries.py",
    "tests/test_inbox_edit_transaction_boundaries.py",
    "tests/test_conversation_summary_transactions.py",
    "tests/test_job_embedding_delivery.py",
]


def test_c043_integration_evidence_modules_exist() -> None:
    missing = [rel for rel in C043_INTEGRATION_EVIDENCE if not (ROOT / rel).is_file()]
    assert not missing, missing


def test_reconcile_send_attempt_closes_db_before_kwork_fetch() -> None:
    path = ROOT / "app/services/send_attempt_reconcile.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "reconcile_send_attempt"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    body_start = func.body[0].lineno - 1
    body_end = func.end_lineno
    body = "\n".join(lines[body_start:body_end])
    with_idx = body.index("async with AsyncSessionLocal()")
    return_idx = body.index("return await _reconcile_inbox_reply")
    assert return_idx > with_idx
    # Session context ends before network reconcile helpers run.
    between = body[with_idx:return_idx]
    assert between.count("async with AsyncSessionLocal()") == 1


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated DB required",
)
async def test_inbox_reconcile_kwork_fetch_without_open_reconcile_session(monkeypatch):
    from app.config import Settings
    from app.db.models import Job, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_KIND_INBOX_REPLY, ATTEMPT_UNKNOWN_RESULT
    from app.db.session import AsyncSessionLocal
    from app.services import send_attempt_reconcile as reconcile

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

    monkeypatch.setattr(reconcile, "AsyncSessionLocal", Tracked)

    async def create_kwork_api(_settings):
        active["during_fetch"] = active["count"]
        api = SimpleNamespace(
            get_actor=AsyncMock(return_value={"id": 1, "username": "seller"}),
            get_messages=AsyncMock(return_value=[]),
            close=AsyncMock(),
        )
        return api

    monkeypatch.setattr(reconcile, "create_kwork_api", create_kwork_api)

    job_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=str(job_id),
                title="T",
                description="d",
                status="sent",
            )
        )
        session.add(
            SendAttempt(
                id=attempt_id,
                kind=ATTEMPT_KIND_INBOX_REPLY,
                idempotency_key=f"inbox:{attempt_id}",
                job_id=job_id,
                snapshot_content="Answer",
                snapshot_version=1,
                status=ATTEMPT_UNKNOWN_RESULT,
                snapshot_metadata={"username": "buyer"},
                started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
        await session.commit()

    outcome = await reconcile.reconcile_send_attempt(
        attempt_id,
        settings=Settings(_env_file=None, PLATFORM_DRY_RUN=False),
    )
    assert outcome == "not_found"
    assert active.get("during_fetch") == 0


async def test_proposal_sender_invokes_browser_without_open_db_session(monkeypatch):
    from app.config import Settings
    from app.services import proposal_sender

    active = {"count": 0}

    class Tracked:
        async def __aenter__(self):
            self.session = proposal_sender.AsyncSessionLocal()
            await self.session.__aenter__()
            active["count"] += 1
            return self.session

        async def __aexit__(self, *args):
            try:
                return await self.session.__aexit__(*args)
            finally:
                active["count"] -= 1

    monkeypatch.setattr(proposal_sender, "AsyncSessionLocal", Tracked)
    observed: list[int] = []

    job = SimpleNamespace(
        id=uuid.uuid4(),
        platform="kwork",
        url="https://kwork.ru/projects/1/view",
        budget_min=100,
        budget_max=200,
        title="T",
    )
    proposal = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        proposal_sender,
        "get_settings",
        lambda: Settings(_env_file=None, PLATFORM_DRY_RUN=False),
    )
    monkeypatch.setattr(proposal_sender, "is_kwork_paused", lambda: False)

    async def send_kwork_offer(*_args, **_kwargs):
        observed.append(active["count"])
        return False, "blocked", {"ambiguous": True}

    monkeypatch.setattr(proposal_sender, "send_kwork_offer", send_kwork_offer)

    await proposal_sender.ProposalSender()._send_to_platform(
        job,
        proposal,
        "Exact offer text",
        approved_payload={
            "content": "Exact offer text",
            "offer_price": 5000,
            "duration_days": 3,
            "submission_action": "new_offer",
        },
    )
    assert observed == [0]
