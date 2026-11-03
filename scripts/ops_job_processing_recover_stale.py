"""Run recover_stale_job_claims (read-only unless --apply triggers recovery)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import structlog

from scripts.ops_json_cli import quiet_sqlalchemy_logs

logger = structlog.get_logger(__name__)


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_recover(*, apply: bool) -> dict:
    from app.config import get_settings
    from app.services.job_processing import recover_stale_job_claims
    from scripts.ops_job_processing_drill_preflight import run_preflight

    settings = get_settings()
    before = await run_preflight(sample_limit=5)
    recovered = 0
    if apply:
        recovered = await recover_stale_job_claims(settings)
    after = await run_preflight(sample_limit=5)
    return {
        "apply": apply,
        "stale_claims_recovered": recovered,
        "lease_seconds": settings.job_processing_lease_seconds,
        "before": {
            "stale_claims_pending_recovery": before.get("stale_claims_pending_recovery"),
            "active_processing_claims": before.get("active_processing_claims"),
        },
        "after": {
            "stale_claims_pending_recovery": after.get("stale_claims_pending_recovery"),
            "active_processing_claims": after.get("active_processing_claims"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Call recover_stale_job_claims (may re-enqueue stale jobs once)",
    )
    args = parser.parse_args()
    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(run_recover(apply=args.apply))
    except Exception as exc:
        logger.error("recover stale failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
