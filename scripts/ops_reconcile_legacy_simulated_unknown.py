#!/usr/bin/env python3
"""Resolve historical unknown_result rows from simulated commit failures (Oct 2025).

These attempts have last_error ``commit_failed_after_platform_success: simulated``
and never received ``platform_response.dry_run``; reconcile can confirm them as
dry-run deliveries without external resend. Does not touch other unknown_result rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url

LEGACY_ERROR = "commit_failed_after_platform_success: simulated"


def _require_working_database() -> None:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    name = make_url(url).database or ""
    if name.endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        print(f"Refusing *_test database {name!r} without JOBPILOT_ALLOW_TEST_URL=1", file=sys.stderr)
        raise SystemExit(2)


async def _legacy_attempts() -> list[dict[str, Any]]:
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_UNKNOWN_RESULT
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(SendAttempt)
                .where(SendAttempt.status == ATTEMPT_UNKNOWN_RESULT)
                .order_by(SendAttempt.created_at)
            )
        ).all()
        out: list[dict[str, Any]] = []
        for attempt in rows:
            err = (attempt.last_error or "").strip()
            if not err.startswith(LEGACY_ERROR):
                continue
            job = await session.get(Job, attempt.job_id) if attempt.job_id else None
            proposal = await session.get(Proposal, attempt.proposal_id) if attempt.proposal_id else None
            out.append(
                {
                    "send_attempt_id": str(attempt.id),
                    "kind": attempt.kind,
                    "job_id": str(attempt.job_id) if attempt.job_id else None,
                    "proposal_id": str(attempt.proposal_id) if attempt.proposal_id else None,
                    "job_status": job.status if job else None,
                    "proposal_status": proposal.status if proposal else None,
                    "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
                    "has_dry_run_flag": bool((attempt.platform_response or {}).get("dry_run")),
                }
            )
        return out


async def _apply_one(attempt_id: uuid.UUID) -> str:
    from app.config import Settings, get_settings

    os.environ["PLATFORM_DRY_RUN"] = "true"
    get_settings.cache_clear()

    from app.db.models import SendAttempt
    from app.db.send_attempt_states import ATTEMPT_UNKNOWN_RESULT
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_reconcile import reconcile_send_attempt

    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, attempt_id)
        if not attempt or attempt.status != ATTEMPT_UNKNOWN_RESULT:
            return "skipped"
        err = (attempt.last_error or "").strip()
        if not err.startswith(LEGACY_ERROR):
            return "skipped"
        merged = dict(attempt.platform_response or {})
        if merged.get("dry_run") is not True:
            merged["dry_run"] = True
            merged["legacy_simulated_reconcile"] = True
            attempt.platform_response = merged
            await session.commit()

    settings = Settings(_env_file=None, PLATFORM_DRY_RUN=True)
    outcome = await reconcile_send_attempt(attempt_id, settings=settings)
    return outcome


async def _run(*, apply: bool) -> dict[str, Any]:
    from app.db.session import engine

    try:
        planned = await _legacy_attempts()
        report: dict[str, Any] = {"legacy_candidates": planned, "apply": apply, "results": []}
        if not apply:
            return report
        if os.environ.get("JOBPILOT_OPERATOR_LEGACY_SIMULATED_UNKNOWN") != "1":
            report["error"] = "Set JOBPILOT_OPERATOR_LEGACY_SIMULATED_UNKNOWN=1 to apply"
            return report
        for row in planned:
            aid = uuid.UUID(row["send_attempt_id"])
            outcome = await _apply_one(aid)
            report["results"].append({"send_attempt_id": row["send_attempt_id"], "outcome": outcome})
        return report
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Reconcile matching attempts (requires env gate)")
    args = parser.parse_args()
    _require_working_database()
    report = asyncio.run(_run(apply=args.apply))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("error"):
        return 2
    if args.apply and any(r.get("outcome") != "confirmed" for r in report.get("results") or []):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
