#!/usr/bin/env python3
"""Step 6 preflight: blockers and gates before PLATFORM_DRY_RUN=false (no secrets, no env changes)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKING_DB = os.environ.get(
    "JOBPILOT_WORKING_DATABASE_URL",
    "postgresql+asyncpg://jobpilot:jobpilot_secret@127.0.0.1:5434/jobpilot_ai",
)
SIGNOFF = Path(os.environ.get("JOBPILOT_LIVE_SIGNOFF_PATH", str(ROOT / "data/live_kwork_signoff.json")))


def _run_pilot_status() -> int:
    env = {**os.environ, "JOBPILOT_WORKING_DATABASE_URL": WORKING_DB}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/ops_pilot_status.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    return proc.returncode


async def _unknown_result_note() -> dict:
    os.environ["DATABASE_URL"] = WORKING_DB
    from app.config import get_settings

    get_settings.cache_clear()
    from sqlalchemy import func, select

    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal

    legacy_prefix = "commit_failed_after_platform_success: simulated"
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(SendAttempt)
            .where(SendAttempt.status.in_(("sending", "unknown_result", "needs_attention")))
        )
        legacy = await session.scalar(
            select(func.count())
            .select_from(SendAttempt)
            .where(
                SendAttempt.status == "unknown_result",
                SendAttempt.last_error.like(f"{legacy_prefix}%"),
            )
        )
        rows = (
            await session.scalars(
                select(SendAttempt)
                .where(SendAttempt.status.in_(("sending", "unknown_result", "needs_attention")))
                .order_by(SendAttempt.created_at.desc())
                .limit(3)
            )
        ).all()
    total = int(count or 0)
    legacy_n = int(legacy or 0)
    return {
        "count": total,
        "legacy_simulated_commit_failure": legacy_n,
        "actionable_uncertainty": max(0, total - legacy_n),
        "latest_errors": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "status": r.status,
                "last_error": (r.last_error or "")[:100],
            }
            for r in rows
        ],
        "note": (
            "Includes sending, unknown_result and needs_attention. "
            "Legacy simulated: scripts/ops_reconcile_legacy_simulated_unknown.py"
        ),
    }


def _signoff_gate() -> dict:
    if not SIGNOFF.is_file():
        return {"path": str(SIGNOFF.relative_to(ROOT)), "present": False, "validator": "skip (file missing)"}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/validate_live_signoff.py")],
        cwd=ROOT,
        env={**os.environ, "JOBPILOT_LIVE_SIGNOFF_PATH": str(SIGNOFF)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "path": str(SIGNOFF.relative_to(ROOT)),
        "present": True,
        "validator_exit": proc.returncode,
        "validator_out": (proc.stdout or proc.stderr or "").strip()[:200],
    }


def _qdrant_drill_dry() -> dict:
    env = {**os.environ, "DATABASE_URL": WORKING_DB}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/qdrant_embedding_drill.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if payload is None:
            payload = {"parse_error": proc.stdout[:200]}
    payload["exit_code"] = proc.returncode
    return payload


def _kwork_access_gate() -> dict:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/ops_kwork_access_probe.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    browser_auth_verified = False
    if proc.stdout.strip():
        try:
            report = json.loads(proc.stdout)
            if isinstance(report, list):
                for row in report:
                    if row.get("browser_auth_verified"):
                        browser_auth_verified = True
                        break
        except json.JSONDecodeError:
            pass
    return {
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "browser_auth_verified": browser_auth_verified,
    }


def main() -> int:
    print("== Step 6 preflight (no live send) ==\n")
    pilot_status = _run_pilot_status()
    print()
    unknown = asyncio.run(_unknown_result_note())
    print("unknown_result:", json.dumps(unknown, ensure_ascii=False, indent=2))
    print()
    signoff = _signoff_gate()
    print("live_signoff:", json.dumps(signoff, ensure_ascii=False, indent=2))
    print()
    drill = _qdrant_drill_dry()
    print("qdrant_embedding_drill (dry):", json.dumps(drill, ensure_ascii=False, indent=2))
    print()
    kwork_access = _kwork_access_gate()
    print("kwork_access_probe:", json.dumps(kwork_access, ensure_ascii=False, indent=2))
    print()
    print(
        "Ready for live ONLY when: offers pause lifted (if new proposals), "
        "signoff validator exit 0, explicit owner OK, PLATFORM_DRY_RUN=false for ONE send."
    )
    blockers = []
    if pilot_status != 0:
        blockers.append("pilot_status_failed")
    if type(unknown.get("count")) is not int:
        blockers.append("unknown_result_requires_resolution")
    else:
        legacy_n = int(unknown.get("legacy_simulated_commit_failure") or 0)
        actionable = unknown.get("actionable_uncertainty")
        if actionable is None:
            actionable = max(0, int(unknown.get("count") or 0) - legacy_n)
        else:
            actionable = int(actionable)
        if actionable > 0:
            blockers.append("unknown_result_requires_resolution")
        elif legacy_n > 0:
            blockers.append("legacy_simulated_unknown_pending_reconcile")
    if drill.get("qdrant", {}).get("ok") is not True or drill.get("exit_code") != 0:
        blockers.append("qdrant")
    os.environ["DATABASE_URL"] = WORKING_DB
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services.kwork_pause import is_kwork_offers_paused

    if is_kwork_offers_paused():
        blockers.append("kwork_offers_paused")
    if not signoff.get("present") or signoff.get("validator_exit") != 0:
        blockers.append("live_signoff_not_valid")
    if not kwork_access.get("ok"):
        blockers.append("kwork_access_blocked")
    if kwork_access.get("browser_auth_verified") is not True:
        blockers.append("kwork_browser_auth_unverified")
    if blockers:
        print(f"Blockers: {', '.join(blockers)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
