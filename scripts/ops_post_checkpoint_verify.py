"""Post-checkpoint embedding search verification (read-only, no reindex)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_LIMIT = 50


def _require_working_url() -> None:
    url = os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def run_post_checkpoint(limit: int) -> dict:
    if str(os.path.dirname(__file__)) not in sys.path:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    from scripts.embedding_queue_inventory import run_inventory as embedding_inventory
    from scripts.verify_pilot_embedding_search import verify

    inv = await embedding_inventory(include_legacy_unsigned=True)
    search = await verify(None, limit)
    return {
        "checkpoint_note": "Run after signed_percent_of_total >= ~50; does not enqueue reindex.",
        "embedding_inventory": {
            "signed_percent_of_total": inv.get("signed_percent_of_total"),
            "jobs_with_current_signature": inv.get("jobs_with_current_signature"),
            "legacy_unsigned": inv.get("legacy_indexed_without_signature"),
        },
        "search_verify": search,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    from scripts.ops_json_cli import quiet_sqlalchemy_logs

    quiet_sqlalchemy_logs()
    _require_working_url()
    try:
        report = asyncio.run(run_post_checkpoint(max(1, args.limit)))
    except Exception as exc:
        logger.error("post checkpoint verify failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = report.get("search_verify", {}).get("ok") is True
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
