"""Read-only notification outbox inventory (no Telegram sends)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import InboundMessage, NotificationOutbox
from app.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

OPERATOR_HINTS_BY_KIND: dict[str, str] = {
    "proposal_approval": "Сверить Telegram UI; при необходимости /rereview — не resend Kwork без reconcile.",
    "inbox_alert": "Сверить partial delivery; reissue inbox card per operator-review-reissue — не blind resend.",
    "ops_alert": "Generation exhausted; ops_inbound_recovery.py или ручная проверка inbound.",
}


def error_bucket(last_error: str | None) -> str:
    err = (last_error or "").lower()
    if "timeout" in err or "timed out" in err:
        return "timeout"
    if "lease_expired" in err:
        return "lease_expired"
    if "partial" in err:
        return "partial_delivery"
    if not err:
        return "unknown"
    return "other"


def _count_by_reference_type(rows: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        rt = row.reference_type or "unknown"
        counts[rt] = counts.get(rt, 0) + 1
    return counts


def _build_triage_groups(rows: list) -> list[dict]:
    """Group needs_attention rows for operator checklists (no payload bodies)."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        bucket = error_bucket(row.last_error)
        key = (row.kind, bucket)
        grouped.setdefault(key, []).append(
            {
                "outbox_id": str(row.id),
                "reference_type": row.reference_type,
                "reference_id": str(row.reference_id) if row.reference_id else None,
                "telegram_message_id": row.telegram_message_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    result: list[dict] = []
    for (kind, bucket), items in sorted(grouped.items()):
        result.append(
            {
                "kind": kind,
                "error_bucket": bucket,
                "count": len(items),
                "operator_hint": OPERATOR_HINTS_BY_KIND.get(kind, "Manual triage; no auto-resend."),
                "items": items[:25],
                "items_truncated": max(0, len(items) - 25),
            }
        )
    return result


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_inventory(*, pending_limit: int) -> dict:
    from urllib.parse import urlparse

    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    settings = get_settings()
    url = (
        os.environ.get("JOBPILOT_WORKING_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )
    db_name = urlparse(url).path.lstrip("/") or "unknown"
    if db_name.endswith("_test") and os.environ.get("JOBPILOT_ALLOW_TEST_URL") != "1":
        print(
            f"Warning: inventory targets {db_name}; set JOBPILOT_WORKING_DATABASE_URL for jobpilot_ai",
            file=sys.stderr,
        )

    async with AsyncSessionLocal() as session:
        counts = await session.execute(
            select(NotificationOutbox.kind, NotificationOutbox.status, func.count())
            .group_by(NotificationOutbox.kind, NotificationOutbox.status)
            .order_by(NotificationOutbox.kind, NotificationOutbox.status)
        )
        by_kind_status = [
            {"kind": kind, "status": status, "count": int(count)}
            for kind, status, count in counts.all()
        ]
        pending_ops = list(
            (
                await session.scalars(
                    select(NotificationOutbox)
                    .where(
                        NotificationOutbox.kind == "ops_alert",
                        NotificationOutbox.status.in_(("pending", "sending")),
                    )
                    .order_by(NotificationOutbox.created_at)
                    .limit(pending_limit)
                )
            ).all()
        )
        inbound_needs = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(InboundMessage)
                    .where(InboundMessage.status == "needs_attention")
                )
            ).scalar()
            or 0
        )
        needs_attention_all = list(
            (
                await session.scalars(
                    select(NotificationOutbox)
                    .where(NotificationOutbox.status == "needs_attention")
                    .order_by(NotificationOutbox.created_at.desc())
                )
            ).all()
        )
        needs_attention_rows = needs_attention_all[:pending_limit]

    def _error_preview(row: NotificationOutbox) -> str | None:
        err = row.last_error
        if not err:
            return None
        text = str(err).replace("\n", " ")
        return text[:160] + ("…" if len(text) > 160 else "")

    needs_attention_totals_by_error_bucket: dict[str, int] = {}
    needs_attention_totals_by_kind: dict[str, int] = {}
    for row in needs_attention_all:
        needs_attention_totals_by_kind[row.kind] = needs_attention_totals_by_kind.get(row.kind, 0) + 1
        bucket = error_bucket(row.last_error)
        needs_attention_totals_by_error_bucket[bucket] = (
            needs_attention_totals_by_error_bucket.get(bucket, 0) + 1
        )

    needs_attention_sample_by_error_bucket: dict[str, int] = {}
    for row in needs_attention_rows:
        bucket = error_bucket(row.last_error)
        needs_attention_sample_by_error_bucket[bucket] = (
            needs_attention_sample_by_error_bucket.get(bucket, 0) + 1
        )

    triage_groups = _build_triage_groups(needs_attention_all)

    return {
        "database_name": db_name,
        "by_kind_status": by_kind_status,
        "inbound_needs_attention": inbound_needs,
        "pending_ops_alert": [
            {
                "id": str(row.id),
                "status": row.status,
                "idempotency_key": row.idempotency_key,
                "reference_id": str(row.reference_id),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in pending_ops
        ],
        "outbox_needs_attention_sample": [
            {
                "id": str(row.id),
                "kind": row.kind,
                "reference_type": row.reference_type,
                "reference_id": str(row.reference_id),
                "telegram_message_id": row.telegram_message_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "last_error_preview": _error_preview(row),
                "error_bucket": error_bucket(row.last_error),
                "operator_hint": OPERATOR_HINTS_BY_KIND.get(row.kind, "Manual triage; no auto-resend."),
            }
            for row in needs_attention_rows
        ],
        "needs_attention_total": len(needs_attention_all),
        "needs_attention_totals_by_kind": needs_attention_totals_by_kind,
        "needs_attention_totals_by_error_bucket": needs_attention_totals_by_error_bucket,
        "needs_attention_by_reference_type": _count_by_reference_type(needs_attention_all),
        "needs_attention_sample_by_error_bucket": needs_attention_sample_by_error_bucket,
        "needs_attention_triage_groups": triage_groups,
        "operator_hints_by_kind": OPERATOR_HINTS_BY_KIND,
        "notes": {
            "needs_attention_no_auto_resend": True,
            "ops_alert_pending_is_operator_generation_exhausted": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pending-limit", type=int, default=20)
    args = parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_database_url()
    try:
        report = asyncio.run(run_inventory(pending_limit=max(1, args.pending_limit)))
    except Exception as exc:
        logger.error("outbox inventory failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
