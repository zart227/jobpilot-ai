"""Report (and optionally fix) multiple active proposals per job before UNIQUE migration."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

import structlog
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import Proposal
from app.db.workflow_states import PROPOSAL_ACTIVE_STATUSES, PROPOSAL_STATUS_SUPERSEDED
from app.services.proposal_conflict_resolution import (
    JobProposalConflictPlan,
    ProposalConflictRow,
    plan_active_proposal_conflicts,
)

logger = structlog.get_logger(__name__)

_ACTIVE_IN_SQL = ", ".join(f"'{s}'" for s in sorted(PROPOSAL_ACTIVE_STATUSES))

FETCH_ACTIVE_PROPOSALS = f"""
    SELECT p.id, p.job_id, p.status, p.created_at
    FROM proposals p
    WHERE p.status IN ({_ACTIVE_IN_SQL})
    ORDER BY p.job_id, p.created_at DESC
"""


def _plan_to_dict(plan: JobProposalConflictPlan) -> dict:
    return {
        "job_id": str(plan.job_id),
        "keep_proposal_id": str(plan.keep_proposal_id),
        "supersede_proposal_ids": [str(pid) for pid in plan.supersede_proposal_ids],
        "proposals": [
            {
                "id": str(p.id),
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in plan.proposals
        ],
    }


async def fetch_active_proposals(session: AsyncSession) -> list[ProposalConflictRow]:
    result = await session.execute(text(FETCH_ACTIVE_PROPOSALS))
    return [ProposalConflictRow.from_mapping(dict(row._mapping)) for row in result]


async def apply_supersede(session: AsyncSession, proposal_ids: list[uuid.UUID]) -> int:
    if not proposal_ids:
        return 0
    result = await session.execute(
        update(Proposal)
        .where(Proposal.id.in_(proposal_ids))
        .where(Proposal.status.in_(tuple(PROPOSAL_ACTIVE_STATUSES)))
        .values(status=PROPOSAL_STATUS_SUPERSEDED)
    )
    return result.rowcount or 0


async def run_report() -> tuple[list[JobProposalConflictPlan], int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        rows = await fetch_active_proposals(session)
        plans = plan_active_proposal_conflicts(rows)

    await engine.dispose()
    supersede_count = sum(len(p.supersede_proposal_ids) for p in plans)
    return plans, supersede_count


async def run_apply(plans: list[JobProposalConflictPlan]) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    all_ids = [pid for plan in plans for pid in plan.supersede_proposal_ids]
    async with session_factory() as session, session.begin():
        updated = await apply_supersede(session, all_ids)
    await engine.dispose()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Set non-keeper active proposals to superseded (requires --confirm)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required with --apply to avoid accidental writes",
    )
    args = parser.parse_args()

    if args.apply and not args.confirm:
        print("Refusing --apply without --confirm", file=sys.stderr)
        return 2

    try:
        plans, supersede_count = asyncio.run(run_report())
    except Exception as exc:
        logger.error("resolve_active_proposal_conflicts failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    payload = {
        "conflict_jobs": len(plans),
        "proposals_to_supersede": supersede_count,
        "plans": [_plan_to_dict(p) for p in plans],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not plans:
        print("No active proposal conflicts.")
        return 0

    if not args.apply:
        print(
            f"\n{len(plans)} job(s) with multiple active proposals. "
            "Re-run with --apply --confirm after backup to supersede extras.",
            file=sys.stderr,
        )
        return 1

    try:
        updated = asyncio.run(run_apply(plans))
    except Exception as exc:
        logger.error("apply supersede failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(f"Updated {updated} proposal(s) to superseded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
