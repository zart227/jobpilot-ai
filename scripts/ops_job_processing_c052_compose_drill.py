"""Controlled compose C052 drill: fake job hold + hard worker stop (working/staging only)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid

import structlog

from scripts.ops_json_cli import parse_json_stdout, quiet_sqlalchemy_logs

logger = structlog.get_logger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE_DRILL = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.c052-drill.yml",
]


def _require_database_url() -> None:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("JOBPILOT_WORKING_DATABASE_URL")):
        print("Set DATABASE_URL or JOBPILOT_WORKING_DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)


def _run(cmd: list[str], *, cwd: str = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _compose(*args: str) -> subprocess.CompletedProcess:
    return _run(["docker", "compose", *args], cwd=ROOT)


def _compose_drill(*args: str) -> subprocess.CompletedProcess:
    return _run([*COMPOSE_DRILL, *args], cwd=ROOT)


def _json_from_exec(script: str, *script_args: str) -> dict:
    proc = _compose_drill("exec", "-T", "api", "python", script, *script_args)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "exec failed")
    return parse_json_stdout(proc.stdout)


async def _seed_fake_job() -> uuid.UUID:
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="fake",
                external_id=f"c052-drill-{job_id}",
                title="C052 compose drill hold",
                description="Synthetic job for worker crash drill; platform fake only.",
                status="new",
            )
        )
        await session.commit()
    return job_id


async def _journal_events(job_id: uuid.UUID) -> list[str]:
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            return []
        raw = job.raw_data or {}
        journal = raw.get("processing_attempt_journal") or []
        return [row.get("event") for row in journal if isinstance(row, dict)]


async def run_drill(*, confirm: str, skip_compose_reset: bool) -> dict:
    from app.config import get_settings
    from scripts.ops_json_cli import rebind_async_engine

    await rebind_async_engine()
    settings = get_settings()
    if confirm != "c052-compose-drill":
        raise ValueError("Pass --confirm c052-compose-drill")
    if not settings.platform_dry_run:
        raise ValueError("PLATFORM_DRY_RUN must be true for compose C052 drill")

    quiet_sqlalchemy_logs()
    report: dict = {"steps": []}

    report["steps"].append("compose_up_drill_overlay")
    up = _compose_drill("up", "-d", "celery-worker", "celery-beat", "api")
    if up.returncode != 0:
        raise RuntimeError(up.stderr or up.stdout)
    await asyncio.sleep(8)

    job_id = await _seed_fake_job()
    report["job_id"] = str(job_id)

    before = _json_from_exec("scripts/ops_job_processing_drill_record.py", "--phase", "before", "--job-id", str(job_id))
    report["before"] = before

    report["steps"].append("enqueue_process_single_job")
    enqueue = _compose_drill(
        "exec",
        "-T",
        "celery-worker",
        "celery",
        "-A",
        "app.celery_app",
        "call",
        "app.tasks.scrape_tasks.process_single_job",
        f"--args=[\"{job_id}\"]",
    )
    if enqueue.returncode != 0:
        raise RuntimeError(enqueue.stderr or enqueue.stdout)

    claim_seen = False
    for _ in range(40):
        pre = _json_from_exec("scripts/ops_job_processing_drill_preflight.py")
        active = int(pre.get("active_processing_claims") or 0)
        if active >= 1:
            claim_seen = True
            break
        await asyncio.sleep(1)
    report["claim_seen_before_stop"] = claim_seen
    if not claim_seen:
        raise RuntimeError("Active claim not observed; drill aborted before worker stop")

    report["steps"].append("stop_celery_worker")
    stop = _compose_drill("stop", "celery-worker")
    if stop.returncode != 0:
        raise RuntimeError(stop.stderr or stop.stdout)

    report["steps"].append("wait_past_lease")
    await asyncio.sleep(65)

    report["steps"].append("recover_stale")
    recover = _compose_drill(
        "exec",
        "-T",
        "api",
        "python",
        "scripts/ops_job_processing_recover_stale.py",
        "--apply",
    )
    if recover.returncode != 0:
        raise RuntimeError(recover.stderr or recover.stdout)
    report["recover"] = parse_json_stdout(recover.stdout)

    report["steps"].append("start_celery_worker")
    start = _compose_drill("start", "celery-worker")
    if start.returncode != 0:
        raise RuntimeError(start.stderr or start.stdout)
    await asyncio.sleep(5)

    after = _json_from_exec("scripts/ops_job_processing_drill_record.py", "--phase", "after", "--job-id", str(job_id))
    report["after"] = after
    events = await _journal_events(job_id)
    report["journal_event_types"] = events
    report["acceptance"] = {
        "stale_claim_recovered_seen": "stale_claim_recovered" in events,
        "claim_seen_before_stop": claim_seen,
    }

    if not skip_compose_reset:
        report["steps"].append("compose_restore_default")
        restore = _compose("up", "-d", "celery-worker", "celery-beat", "api")
        if restore.returncode != 0:
            report["compose_restore_error"] = restore.stderr or restore.stdout
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", required=True)
    parser.add_argument(
        "--skip-compose-reset",
        action="store_true",
        help="Leave drill overlay env on worker (not recommended)",
    )
    args = parser.parse_args()
    _require_database_url()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        report = asyncio.run(run_drill(confirm=args.confirm, skip_compose_reset=args.skip_compose_reset))
    except Exception as exc:
        logger.error("c052 compose drill failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("acceptance", {}).get("stale_claim_recovered_seen") else 1


if __name__ == "__main__":
    raise SystemExit(main())
