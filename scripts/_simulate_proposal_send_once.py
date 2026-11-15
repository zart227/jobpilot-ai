"""One-shot proposal send for operator verification (uses real PLATFORM_DRY_RUN)."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import select

from app.db.models import Job, Proposal
from app.db.session import AsyncSessionLocal
from app.services.proposal_sender import ProposalSender


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_external_id", help='e.g. kwork-3271907')
    parser.add_argument("--proposal-id", help="UUID; default: latest approved proposal for job")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        job = await session.scalar(
            select(Job).where(Job.external_id == args.job_external_id).limit(1)
        )
        if not job:
            print(json.dumps({"error": "job_not_found"}, ensure_ascii=False))
            return 1
        if args.proposal_id:
            proposal = await session.get(Proposal, uuid.UUID(args.proposal_id))
        else:
            proposal = await session.scalar(
                select(Proposal)
                .where(Proposal.job_id == job.id, Proposal.status == "approved")
                .order_by(Proposal.version.desc())
                .limit(1)
            )
        if not proposal:
            print(json.dumps({"error": "proposal_not_found"}, ensure_ascii=False))
            return 1

    sender = ProposalSender()
    success, error, debug = await sender.send(str(job.id), str(proposal.id), proposal.content)
    out = {
        "job_external_id": args.job_external_id,
        "job_id": str(job.id),
        "proposal_id": str(proposal.id),
        "success": success,
        "error": error,
        "error_label": (debug or {}).get("error_label"),
        "steps_tail": (debug or {}).get("steps", [])[-6:] if debug else [],
        "screenshot": (debug or {}).get("screenshot"),
        "debug_file": (debug or {}).get("debug_file"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
