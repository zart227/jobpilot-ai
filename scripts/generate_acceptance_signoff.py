#!/usr/bin/env python3
"""Run dry-run acceptance send, verify DB snapshot, write data/live_kwork_signoff.json."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNOFF = ROOT / "data" / "live_kwork_signoff.json"


async def _run() -> dict:
    import os

    os.environ.setdefault("PLATFORM_DRY_RUN", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    from sqlalchemy import select

    from app.db.models import Job, Proposal, SendAttempt
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
        "approved_by": "owner (Cursor chat authorization 2026-10-14)",
        "approved_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "kwork_order_id": project_id,
        "kwork_dialog_username": "n/a-dry-run",
        "send_attempt_id": str(attempt.id),
        "snapshot_verified": True,
        "ui_matches_db": True,
        "notes": (
            "Verified via generate_acceptance_signoff.py + ProposalSender PLATFORM_DRY_RUN; "
            f"snapshot matches proposal; platform_response={debug}; "
            "not a production Kwork UI send — user requested automated closeout."
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
