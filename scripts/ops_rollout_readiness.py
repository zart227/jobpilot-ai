"""Read-only rollout readiness: working DB, HTTP ready, outbox metrics, beat schedule."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

import httpx
import structlog

from scripts.ops_json_cli import quiet_sqlalchemy_logs, rebind_async_engine

logger = structlog.get_logger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _require_working_url() -> None:
    if not (os.environ.get("JOBPILOT_WORKING_DATABASE_URL") or os.environ.get("DATABASE_URL")):
        print("Set JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


async def _db_metrics() -> dict:
    from app.db.session import AsyncSessionLocal
    from app.services.ops_metrics import collect_ops_metrics

    async with AsyncSessionLocal() as session:
        return await collect_ops_metrics(session)


def _beat_schedule() -> dict:
    from app.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule or {}
    return {
        name: {
            "task": entry.get("task"),
            "schedule_seconds": int(entry.get("schedule").total_seconds())  # type: ignore[union-attr]
            if hasattr(entry.get("schedule"), "total_seconds")
            else None,
        }
        for name, entry in schedule.items()
    }


async def _http_ready(base_url: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        health = await client.get(f"{base_url.rstrip('/')}/health")
        ready = await client.get(f"{base_url.rstrip('/')}/ready")
    ready_json = ready.json() if ready.status_code == 200 else {}
    return {
        "base_url": base_url,
        "health_status": health.status_code,
        "ready_status": ready.status_code,
        "ready_body_ok": ready.status_code == 200
        and isinstance(ready_json, dict)
        and ready_json.get("ready") is True,
        "ready_degraded": ready_json.get("status") == "degraded",
    }


async def _stats_compare(base_url: str) -> dict | None:
    token = (os.environ.get("API_AUTH_TOKEN") or os.environ.get("JOBPILOT_API_AUTH_TOKEN") or "").strip()
    if not token:
        return {"skipped": True, "reason": "API_AUTH_TOKEN not set"}
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{base_url.rstrip('/')}/stats", headers=headers)
    if resp.status_code != 200:
        return {"skipped": False, "http_status": resp.status_code}
    body = resp.json()
    ops = body.get("ops") if isinstance(body.get("ops"), dict) else {}
    outbox = ops.get("notification_outbox") or body.get("notification_outbox") or {}
    return {
        "skipped": False,
        "http_status": resp.status_code,
        "notification_outbox_needs_attention": outbox.get("needs_attention"),
        "notification_outbox_pending_or_claimed": outbox.get("pending_or_claimed"),
    }


def _alembic_head_via_compose() -> dict:
    if os.environ.get("JOBPILOT_SKIP_COMPOSE_CHECKS") == "1":
        return {"skipped": True}
    proc = subprocess.run([
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "jobpilot",
            "-d",
            "jobpilot_ai",
            "-tAc",
            "SELECT version_num FROM alembic_version",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": "compose postgres query failed"}
    version = (proc.stdout or "").strip()
    from scripts.migration_schema_inventory import EXPECTED_HEAD

    at_head = version == EXPECTED_HEAD
    return {
        "ok": bool(version) and at_head,
        "alembic_version": version,
        "expected_head": EXPECTED_HEAD,
        "at_head": at_head,
    }


def _readiness_issues_from_checks(
    *,
    working: dict,
    http: dict,
    beat: dict,
    alembic: dict,
    metrics: dict,
    stats: dict | None,
) -> list[str]:
    issues: list[str] = []
    if not working.get("ok"):
        issues.extend(working.get("issues") or [])
    if not http.get("ready_body_ok"):
        issues.append(f"/ready not OK (HTTP {http.get('ready_status')})")
    if not beat.get("deliver-notification-outbox"):
        issues.append("celery beat missing deliver-notification-outbox schedule")
    if alembic.get("skipped"):
        issues.append("compose alembic check skipped (set JOBPILOT_SKIP_COMPOSE_CHECKS=0 for rollout)")
    elif not alembic.get("at_head"):
        issues.append(
            "alembic head mismatch: "
            f"compose={alembic.get('alembic_version')!r} expected {alembic.get('expected_head')!r}"
        )
    elif not alembic.get("ok"):
        issues.append("alembic version missing on compose database")
    if stats:
        if stats.get("skipped"):
            issues.append("stats_api_compare skipped (set API_AUTH_TOKEN for rollout gate)")
        elif stats.get("http_status") not in (None, 200):
            issues.append(f"/stats compare failed (HTTP {stats.get('http_status')})")
        elif stats.get("http_status") == 200:
            db_na = int((metrics.get("notification_outbox") or {}).get("needs_attention") or 0)
            api_na = stats.get("notification_outbox_needs_attention")
            if api_na is not None and int(api_na) != db_na:
                issues.append(
                    f"stats vs ops_metrics needs_attention mismatch db={db_na} api={api_na}"
                )
    return issues


async def run_readiness(*, base_url: str) -> dict:
    from scripts.ops_working_db_preflight import run_preflight

    await rebind_async_engine()
    working = await run_preflight()
    from app.config import get_settings

    settings = get_settings()
    db_name = urlparse(
        os.environ.get("JOBPILOT_WORKING_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    ).path.lstrip("/")
    metrics = await _db_metrics()
    http = await _http_ready(base_url)
    stats = await _stats_compare(base_url)
    beat = _beat_schedule()
    alembic = _alembic_head_via_compose()
    issues = _readiness_issues_from_checks(
        working=working,
        http=http,
        beat=beat,
        alembic=alembic,
        metrics=metrics,
        stats=stats,
    )
    ok = len(issues) == 0
    warnings: list[str] = []
    na_count = int((metrics.get("notification_outbox") or {}).get("needs_attention") or 0)
    if na_count:
        warnings.append(
            f"notification_outbox.needs_attention={na_count}; "
            "run make -s ops-outbox-triage / ops-outbox-triage-md before live sends"
        )
    return {
        "ok": ok,
        "warnings": warnings,
        "database_name": db_name,
        "platform_dry_run": settings.platform_dry_run,
        "working_db_preflight": working,
        "http": http,
        "alembic": alembic,
        "celery_beat_schedule": beat,
        "ops_metrics": metrics,
        "stats_api_compare": stats,
        "issues": issues,
        "notes": [
            "Does not restart services or send Telegram/Kwork.",
            "Outbox needs_attention requires manual triage (make ops-outbox-triage).",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("JOBPILOT_API_URL", "http://127.0.0.1:8000"),
    )
    args = parser.parse_args()
    quiet_sqlalchemy_logs()
    _require_working_url()
    try:
        report = asyncio.run(run_readiness(base_url=args.base_url))
    except Exception as exc:
        logger.error("rollout readiness failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
