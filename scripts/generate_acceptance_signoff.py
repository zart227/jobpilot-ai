#!/usr/bin/env python3
"""Run dry-run acceptance send, verify DB snapshot, write isolated simulation evidence."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNOFF = ROOT / "data" / "simulation_signoff.json"


async def _run() -> dict:
    import os

    from sqlalchemy.engine import make_url

    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        raise RuntimeError("Simulation requires explicit isolated integration environment")
    if not (make_url(os.environ.get("DATABASE_URL", "")).database or "").endswith("_test"):
        raise RuntimeError("Refusing to write simulation records to a working database")
    os.environ["PLATFORM_DRY_RUN"] = "true"
    os.environ["DEBUG"] = "false"
    from app.config import get_settings

    get_settings.cache_clear()

    from sqlalchemy import select

    from app.db.models import Client, Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.proposal_sender import ProposalSender

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    project_id = f"accept-{job_id.hex[:8]}"
    content = "Acceptance signoff verification proposal (PLATFORM_DRY_RUN)."

    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"kwork-{project_id}",
                title="Acceptance signoff job",
                description="Automated remediation closeout",
                url=f"https://kwork.ru/projects/{project_id}/view",
                status="approved",
                budget_min=1000,
                budget_max=2000,
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content=content,
                status="approved",
                version=1,
            )
        )
        await session.commit()

    # Synthetic approval fixture only after the explicit *_test/integration guard above.
    # This script never produces a live human authorization/signoff.
    from app.services.approval_snapshot import capture_proposal_card, record_proposal_approval
    from app.services.opportunity_policy import kwork_opportunity_fingerprint

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        proposal = await session.get(Proposal, proposal_id)
        client = Client(platform="kwork", external_id=f"simulation-{job_id}",
                        metadata_={"projects_posted": 5, "hire_percent": 50, "stats_source": "kwork_buyer_block_v1"})
        session.add(client)
        await session.flush()
        job.client_id = client.id
        facts = {
            "id": str(job.id), "client_id": str(client.id), "platform": job.platform, "title": job.title,
            "description": job.description, "budget_min": float(job.budget_min),
            "budget_max": float(job.budget_max), "budget_currency": job.budget_currency,
            "url": job.url, "deadline": job.deadline.isoformat() if job.deadline else None,
            "skills": job.skills or [],
            "client_projects_posted": 5, "client_hire_percent": 50, "client_stats_source": "kwork_buyer_block_v1",
        }
        job.raw_data = {"opportunity_assessment": {
            "relevant": True, "assessment_id": "e48e15f8-6d76-4076-9631-78bf927c5719", "source_fingerprint": kwork_opportunity_fingerprint(facts),
            "estimated_hours": 1, "estimated_calendar_days": 1, "scope_clear": True,
            "complexity": "simple", "minimum_price_rub": 500,
        }}
        pending_id = uuid.uuid4()
        await capture_proposal_card(session, pending_id, job, proposal)
        await record_proposal_approval(
            session,
            pending_id,
            job,
            proposal,
            actor_user_id=1,
            chat_id=1,
            message_id=1,
        )
        await session.commit()

    sender = ProposalSender()
    ok, error, debug = await sender.send(str(job_id), str(proposal_id), content)
    if not ok:
        raise RuntimeError(f"dry-run send failed: {error}")

    async with AsyncSessionLocal() as session:
        attempt = (
            await session.execute(
                select(SendAttempt).where(
                    SendAttempt.proposal_id == proposal_id,
                    SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
                )
            )
        ).scalar_one()
        proposal = await session.get(Proposal, proposal_id)
        assert proposal is not None
        snapshot_ok = attempt.snapshot_content.strip() == content.strip()
        db_ok = proposal.status == "sent" and proposal.content.strip() == content.strip()
        if not snapshot_ok or not db_ok:
            raise RuntimeError("snapshot/DB mismatch after dry-run send")

    return {
        "schema_version": 2,
        "mode": "simulation",
        "generated_at": datetime.now(UTC).isoformat(),
        "kwork_order_id": project_id,
        "kwork_dialog_username": "n/a-dry-run",
        "send_attempt_id": str(attempt.id),
        "snapshot_verified": True,
        "ui_matches_db": False,
        "notes": (
            "Verified via generate_acceptance_signoff.py + ProposalSender PLATFORM_DRY_RUN; "
            f"snapshot matches proposal; platform_response={debug}; "
            "No live UI verification or human authorization is claimed."
        ),
    }


def main() -> int:
    payload = asyncio.run(_run())
    SIGNOFF.parent.mkdir(parents=True, exist_ok=True)
    SIGNOFF.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        SIGNOFF.chmod(0o600)
    except OSError:
        pass
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Wrote {SIGNOFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
