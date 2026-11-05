"""C112: rejection reasons, buyer source, operator override journal."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

OVERRIDE = ROOT / "tests/test_operator_override_journal.py"
FRESHNESS = ROOT / "tests/test_job_freshness.py"
REISSUE = ROOT / "tests/test_review_reissue.py"

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated database required",
)


def test_c112_evidence_modules_present() -> None:
    assert OVERRIDE.is_file()
    assert FRESHNESS.is_file()


def test_format_job_decision_context_surfaces_reason_and_audit() -> None:
    from app.db.models import Client, Job
    from app.telegram.bot import format_job_decision_context, format_kwork_skipped_message

    client = Client(id=uuid.uuid4(), platform="kwork", external_id="buyer-99", name="buyer")
    job = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="k-1",
        title="T",
        description="d",
        status="filtered",
        relevance_reason="Не подходит по стеку",
        client_id=client.id,
        client=client,
        raw_data={
            "decision_audit": [
                {"action": "filtered", "reason": "stack", "source": "filter"},
            ]
        },
    )
    ctx = format_job_decision_context(job)
    assert "Не подходит" in ctx
    assert "Журнал решений" in ctx
    assert "buyer-99" in ctx
    skipped = format_kwork_skipped_message(job, reason="Пропуск оператором")
    assert "Журнал решений" in skipped


def _integration_env() -> dict[str, str]:
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
    return env


def test_c112_override_journal_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(OVERRIDE), str(FRESHNESS), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C112 journal bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )


@pytest.mark.asyncio
async def test_c065_bind_writes_operator_override_journal() -> None:
    from app.db.session import AsyncSessionLocal
    from app.services import kwork_inbox
    from app.services.operator_override_journal import list_operator_overrides

    inbound_id = uuid.uuid4()
    job_id = uuid.uuid4()
    ext = f"ext-{job_id.hex[:12]}"
    from app.db.models import InboundMessage, Job

    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=ext,
                title="j",
                description="d",
                status="sent",
            )
        )
        session.add(
            InboundMessage(
                id=inbound_id,
                platform="kwork",
                message_key=f"1:{uuid.uuid4().hex}",
                thread_key="1",
                kwork_user_id=1,
                kwork_username="b",
                body="hi",
                status="received",
            )
        )
        await session.commit()
        assert await kwork_inbox.bind_inbound_to_job(session, inbound_id, job_id)
        await session.commit()

    async with AsyncSessionLocal() as session:
        rows = await list_operator_overrides(
            session, reference_type="inbound_message", reference_id=inbound_id
        )
        assert len(rows) == 1 and rows[0].action == "bind_inbound_job"


def test_c112_reissue_override_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([
            sys.executable,
            "-m",
            "pytest",
            str(REISSUE),
            "-q",
            "-k",
            "delivery_state_cannot_be_bypassed or parallel_requests_create_one",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C112 reissue bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
