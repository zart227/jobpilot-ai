#!/usr/bin/env python3
"""Print minimal pilot launch checklist status (no secrets)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _http_code(url: str, token: str | None = None) -> int:
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url]
    if token:
        cmd.extend(["-H", f"Authorization: Bearer {token}"])
    try:
        out = subprocess.check_output(cmd, timeout=10, text=True).strip()
        return int(out)
    except (subprocess.SubprocessError, ValueError):
        return 0


def _read_token() -> str | None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("API_AUTH_TOKEN="):
            return line.split("=", 1)[1].strip() or None
    return None


async def _db_counts(database_url: str) -> dict[str, int | str]:
    os.environ["DATABASE_URL"] = database_url
    from app.config import get_settings

    get_settings.cache_clear()
    from sqlalchemy import func, select, text

    from app.db.models import Job, NotificationOutbox, SendAttempt, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.embedding_index_signature import SIGNATURE_KEY
    from app.services.kwork_pause import (
        get_kwork_connects_status_summary,
        get_kwork_pause_reason,
        is_kwork_offers_paused,
    )

    async with AsyncSessionLocal() as session:
        pending_cards = await session.scalar(
            select(func.count()).select_from(TelegramPending).where(TelegramPending.status == "pending")
        )
        outbox_pending = await session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(NotificationOutbox.status == "pending")
        )
        outbox_needs_attention = await session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(NotificationOutbox.status == "needs_attention")
        )
        jobs_with_embedding_signature = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.raw_data[SIGNATURE_KEY].astext.is_not(None))
        )
        unknown_attempts = await session.scalar(
            select(func.count())
            .select_from(SendAttempt)
            .where(SendAttempt.status == "unknown_result")
        )
        step5_ok = await session.scalar(
            select(func.count())
            .select_from(SendAttempt)
            .where(
                SendAttempt.proposal_id
                == select(TelegramPending.proposal_id)
                .where(TelegramPending.id == text("'60e079ce-bf4e-4af1-8215-1a8d88cc2b3e'::uuid"))
                .scalar_subquery(),
                SendAttempt.platform_response["dry_run"].astext == "true",
            )
        )
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
    return {
        "alembic": revision or "?",
        "telegram_pending_cards": int(pending_cards or 0),
        "outbox_pending": int(outbox_pending or 0),
        "outbox_needs_attention": int(outbox_needs_attention or 0),
        "jobs_embedding_signature": int(jobs_with_embedding_signature or 0),
        "send_attempts_unknown_result": int(unknown_attempts or 0),
        "pilot_step5_dry_run_ok": int(step5_ok or 0) > 0,
        "kwork_offers_paused": is_kwork_offers_paused(),
        "kwork_pause_reason": (get_kwork_pause_reason() or "")[:120],
        "kwork_connects_status": get_kwork_connects_status_summary() or "",
    }


def _qdrant_ok() -> tuple[bool, str]:
    try:
        out = subprocess.check_output(
            ["curl", "-sf", "http://127.0.0.1:6333/collections"],
            timeout=5,
            text=True,
        )
        return True, "ok" if '"status":"ok"' in out.replace(" ", "") else out[:80]
    except subprocess.SubprocessError as exc:
        return False, str(exc)


def main() -> int:
    token = _read_token()
    working_url = os.environ.get(
        "JOBPILOT_WORKING_DATABASE_URL",
        "postgresql+asyncpg://jobpilot:jobpilot_secret@127.0.0.1:5434/jobpilot_ai",
    )
    backup = ROOT / "data/backups/jobpilot_ai_20261030_pre_pilot.sql.gz"
    lines = [
        "== JobPilot pilot status ==",
        f"API /health: {_http_code('http://127.0.0.1:8000/health')}",
        f"API /jobs no token: {_http_code('http://127.0.0.1:8000/jobs')}",
        f"API /jobs with token: {_http_code('http://127.0.0.1:8000/jobs', token)}",
        f"Backup present: {backup.is_file()} ({backup.name})",
        f"PLATFORM_DRY_RUN in .env: {'PLATFORM_DRY_RUN=true' in (ROOT / '.env').read_text(encoding='utf-8', errors='replace')}",
    ]
    try:
        db = asyncio.run(_db_counts(working_url))
        lines.extend(
            [
                f"Alembic (working): {db['alembic']}",
                f"Telegram pending cards: {db['telegram_pending_cards']}",
                f"Outbox pending: {db['outbox_pending']}",
                f"Outbox needs_attention (manual triage): {db['outbox_needs_attention']}",
                f"Jobs with embedding signature: {db['jobs_embedding_signature']}",
                f"Send attempts unknown_result: {db['send_attempts_unknown_result']}",
                f"Kwork offers paused: {db['kwork_offers_paused']}",
                f"Pause: {db['kwork_pause_reason']}",
                f"Kwork connects: {db['kwork_connects_status'] or '(unknown — run probe or scrape)'}",
            ]
        )
    except Exception as exc:
        db = {}
        lines.append(f"DB check failed: {exc}")
    q_ok, q_detail = _qdrant_ok()
    lines.append(f"Qdrant /collections: {'ok' if q_ok else 'FAIL'} ({q_detail})")
    step5_line = (
        "Plan step 5: DONE (dry-run APPROVE msg 584; platform_response.dry_run=true)."
        if db.get("pilot_step5_dry_run_ok")
        else "Plan step 5: pending — APPROVE dry-run card and run ops_verify_dry_run_approve.py"
    )
    dry = "PLATFORM_DRY_RUN=true" in (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    step6 = (
        "Plan step 6: PLATFORM_DRY_RUN=false — любой APPROVE уходит live; нужны signoff C109 и заказ без отклика."
        if not dry
        else "Plan step 6: fill live_kwork_signoff.json; validate_live_signoff; then ONE live send (ops_step6_preflight.py)."
    )
    lines.extend(["", step5_line, step6])
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
