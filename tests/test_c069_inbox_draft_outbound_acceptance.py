"""C069: draft generation vs confirmed outbound interaction (isolated DB)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated database required",
)

OUTBOUND_ATOMICITY = ROOT / "tests/test_inbox_outbound_atomicity.py"


def test_c069_evidence_modules_present() -> None:
    assert OUTBOUND_ATOMICITY.is_file()


@pytest.mark.asyncio
async def test_inbox_generation_creates_pending_without_outbound_interaction() -> None:
    """Reply generation must not write outbound history before a confirmed send attempt."""
    from app.config import Settings
    from app.db.models import Interaction, Job, TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services import kwork_inbox
    from app.services.kwork_inbox import ClientInboxMessage

    job_id = uuid.uuid4()
    message_id = uuid.uuid4().hex
    user_id = int(uuid.uuid4().int % 800_000_000) + 100
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"kwork-{job_id.int}",
                title="C069 job",
                description="test",
                status="sent",
            )
        )
        await session.commit()

    msg = ClientInboxMessage(
        user_id=user_id,
        username="buyer",
        message_id=message_id,
        text="Какой срок?",
        timestamp=1,
        dialog={"want_id": str(job_id.int)},
        raw={"MID": message_id, "message": "Какой срок?"},
    )
    await kwork_inbox._capture_inbound(msg)
    settings = Settings(_env_file=None, telegram_admin_chat_id="7")

    with patch(
        "app.services.kwork_inbox._generate_inbox_reply",
        return_value=("question", "Срок 5 рабочих дней."),
    ):
        pending_id = await kwork_inbox._persist_inbound_bundle(
            settings,
            msg,
            target_chat=7,
            my_username="seller",
            my_user_id=1,
        )

    assert pending_id is not None
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, uuid.UUID(pending_id))
        assert pending is not None
        assert pending.draft_reply == "Срок 5 рабочих дней."
        assert pending.status == "pending"
        inbound_only = await session.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(
                Interaction.job_id == job_id,
                Interaction.direction == "inbound",
            )
        )
        outbound = await session.scalar(
            select(func.count())
            .select_from(Interaction)
            .where(
                Interaction.job_id == job_id,
                Interaction.direction == "outbound",
            )
        )
        assert int(inbound_only or 0) >= 1
        assert int(outbound or 0) == 0


def test_c069_outbound_atomicity_regression_bundle() -> None:
    env = os.environ.copy()
    env_path = ROOT / ".jobpilot_integration_test.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key, value)
    env["JOBPILOT_INTEGRATION_TESTS"] = "1"
    proc = subprocess.run([sys.executable, "-m", "pytest", str(OUTBOUND_ATOMICITY), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C069 outbound atomicity bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
