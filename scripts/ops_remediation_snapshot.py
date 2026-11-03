"""Read-only rollup for remediation ops: embedding, outbox, inbound, job processing."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_snapshot(*, sample_limit: int) -> dict:
    if str(os.path.dirname(__file__)) not in sys.path:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)

    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    from scripts.embedding_queue_inventory import run_inventory as embedding_inventory
    from scripts.ops_embedding_progress import run_progress
    from scripts.ops_inbound_generation_inventory import run_inventory as inbound_inventory
    from scripts.ops_job_processing_drill_preflight import run_preflight as job_preflight
    from scripts.ops_notification_outbox_inventory import run_inventory as outbox_inventory
    from scripts.ops_pending_outbox_crosscheck import run_crosscheck as pending_outbox_crosscheck

    embedding = await embedding_inventory(include_legacy_unsigned=True)
    progress = await run_progress(include_legacy_unsigned=False)
    outbox = await outbox_inventory(pending_limit=sample_limit)
    inbound = await inbound_inventory(sample_limit=sample_limit)
    jobs = await job_preflight(sample_limit=min(5, sample_limit))
    pending_x = await pending_outbox_crosscheck()

    triage_summary = [
        {
            "kind": g["kind"],
            "error_bucket": g["error_bucket"],
            "count": g["count"],
        }
        for g in outbox.get("needs_attention_triage_groups") or []
    ]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "embedding": {
            "database_name": embedding.get("database_name"),
            "current_signature": embedding.get("current_signature"),
            "jobs_with_current_signature": embedding.get("jobs_with_current_signature"),
            "signed_percent_of_total": embedding.get("signed_percent_of_total"),
            "legacy_unsigned": embedding.get("legacy_indexed_without_signature"),
            "embedding_pending_sql": embedding.get("embedding_pending_sql"),
            "rollup": progress.get("rollup"),
        },
        "outbox": {
            "needs_attention_total": outbox.get("needs_attention_total"),
            "needs_attention_totals_by_kind": outbox.get("needs_attention_totals_by_kind"),
            "triage_groups": triage_summary,
            "pending_ops_alert_count": len(outbox.get("pending_ops_alert") or []),
        },
        "inbound_kwork": {
            "by_status": inbound.get("kwork_inbound_by_status"),
            "needs_attention_sample_count": len(inbound.get("needs_attention_inbound_sample") or []),
        },
        "job_processing": {
            "active_processing_claims": jobs.get("active_processing_claims"),
            "stale_claims_pending_recovery": jobs.get("stale_claims_pending_recovery"),
            "queue_candidate_jobs": jobs.get("queue_candidate_jobs"),
        },
        "pending_outbox": {
            "pending_total": pending_x.get("pending_total"),
            "pending_delivery_uncertain": pending_x.get("pending_delivery_uncertain"),
            "pending_with_outbox_needs_attention": pending_x.get("pending_with_outbox_needs_attention"),
            "inactive_needs_attention_orphan_count": pending_x.get(
                "inactive_needs_attention_orphan_count"
            ),
            "inactive_needs_attention_by_error_bucket": pending_x.get(
                "inactive_needs_attention_by_error_bucket"
            ),
        },
        "notes": [
            "Read-only snapshot; no Telegram/Kwork sends.",
            "Detail: ops_embedding_progress, ops_notification_outbox_inventory, ops_pending_outbox_crosscheck.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(run_snapshot(sample_limit=max(1, args.sample_limit)))
    except Exception as exc:
        logger.error("remediation snapshot failed", error=str(exc))
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
