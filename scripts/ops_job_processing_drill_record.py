"""Capture read-only job processing drill evidence (before/after compose restart)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def _job_snapshot(job_id: uuid.UUID) -> dict | None:
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            return None
        raw = job.raw_data or {}
        journal = raw.get("processing_attempt_journal") or []
        return {
            "job_id": str(job.id),
            "platform": job.platform,
            "status": job.status,
            "processing_stage": job.processing_stage,
            "processing_attempt_count": job.processing_attempt_count,
            "processing_claim_owner": job.processing_claim_owner,
            "processing_claim_token": str(job.processing_claim_token) if job.processing_claim_token else None,
            "processing_claimed_at": job.processing_claimed_at.isoformat() if job.processing_claimed_at else None,
            "journal_event_types": [row.get("event") for row in journal if isinstance(row, dict)][-20:],
            "journal_length": len(journal) if isinstance(journal, list) else 0,
        }


async def run_record(*, job_id: uuid.UUID | None, phase: str, sample_limit: int) -> dict:
    if str(os.path.dirname(__file__)) not in sys.path:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
    from scripts.ops_job_processing_drill_preflight import run_preflight

    preflight = await run_preflight(sample_limit=sample_limit)
    job = await _job_snapshot(job_id) if job_id else None
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "phase": phase,
        "target_job_id": str(job_id) if job_id else None,
        "preflight": preflight,
        "job": job,
        "acceptance_reminder": [
            "stale_claim_recovered in processing_attempt_journal",
            "single re-enqueue after recover (no duplicate storm)",
            "release_process_job_enqueue / dedup cleared for job id",
        ],
        "next_steps": [
            "Compare before/after JSON for the same target_job_id",
            "Append summary to docs/staging-drill-log.md (no secrets)",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=uuid.UUID, help="Drill target job UUID")
    parser.add_argument(
        "--phase",
        choices=("before", "after"),
        default="before",
        help="Label this capture (run twice around compose worker stop)",
    )
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(
            run_record(job_id=args.job_id, phase=args.phase, sample_limit=max(1, args.sample_limit))
        )
    except Exception as exc:
        logger.error("drill record failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    if args.job_id and report.get("job") is None:
        print(json.dumps({"error": f"job not found: {args.job_id}"}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
