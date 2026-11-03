"""Read-only preflight: JOBPILOT_WORKING_DATABASE_URL vs stale DATABASE_URL."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import structlog
from sqlalchemy.engine import make_url

from scripts.ops_json_cli import quiet_sqlalchemy_logs, rebind_async_engine

logger = structlog.get_logger(__name__)


def _require_working_url() -> str:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    return url


async def run_preflight() -> dict:
    working = os.environ.get("JOBPILOT_WORKING_DATABASE_URL")
    database = os.environ.get("DATABASE_URL")
    if working:
        os.environ["DATABASE_URL"] = working
    await rebind_async_engine()
    from app.config import get_settings

    settings = get_settings()
    target = working or database or settings.database_url
    db_name = make_url(target).database or "unknown"
    issues: list[str] = []
    if db_name.endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        issues.append(f"target database {db_name} looks like a test DB")
    if working and database and working != database:
        issues.append(
            "DATABASE_URL differs from JOBPILOT_WORKING_DATABASE_URL; "
            "ops CLIs use WORKING after rebind — unset stale DATABASE_URL or align both"
        )
    return {
        "ok": len(issues) == 0,
        "database_name": db_name,
        "jobpilot_working_database_url_set": bool(working),
        "database_url_set": bool(database),
        "issues": issues,
        "hint": "Prefer: export JOBPILOT_WORKING_DATABASE_URL=…/jobpilot_ai && make -s ops-snapshot",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    quiet_sqlalchemy_logs()
    _require_working_url()
    try:
        report = asyncio.run(run_preflight())
    except Exception as exc:
        logger.error("working db preflight failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
