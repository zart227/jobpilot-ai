"""Read-only operator snapshot: PostgreSQL embedding backlog + Qdrant collection counts."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

import structlog

from scripts.embedding_queue_inventory import run_inventory as pg_inventory
from scripts.qdrant_collection_inventory import run_inventory as qdrant_inventory

logger = structlog.get_logger(__name__)


async def run_progress(*, include_legacy_unsigned: bool) -> dict:
    pg = await pg_inventory(include_legacy_unsigned=include_legacy_unsigned)
    qdrant = await qdrant_inventory()
    signed = pg["database_jobs_total"] - pg["legacy_indexed_without_signature"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "postgresql": pg,
        "qdrant": qdrant,
        "rollup": {
            "jobs_with_signature_estimate": signed,
            "jobs_legacy_unsigned": pg["legacy_indexed_without_signature"],
            "embedding_pending": pg["embedding_pending_sql"],
            "canonical_qdrant_points": (qdrant.get("summary") or {}).get("canonical_points_total"),
            "legacy_qdrant_points": (qdrant.get("summary") or {}).get("legacy_points_total"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-legacy-unsigned",
        action="store_true",
        help="Include legacy-unsigned jobs in would_mark counts (same as embedding_queue_inventory)",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(run_progress(include_legacy_unsigned=args.include_legacy_unsigned))
    except Exception as exc:
        logger.error("ops embedding progress failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report.get("qdrant", {}).get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
