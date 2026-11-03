#!/usr/bin/env python3
"""Check live evidence and its DB attempt. Never approves or closes checklist items."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNOFF = Path(
    os.environ.get("JOBPILOT_LIVE_SIGNOFF_PATH", str(ROOT / "data/live_kwork_signoff.json"))
)


def _artifact(reference: dict, base: Path) -> bytes:
    if not isinstance(reference, dict):
        raise TypeError("artifact reference must be an object")
    relative = reference.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("artifact path must be relative to signoff directory")
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError("artifact must exist inside signoff directory")
    if path.stat().st_size > 10_000_000:
        raise ValueError("artifact exceeds 10 MB")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference.get("sha256"):
        raise ValueError("artifact hash mismatch")
    return content


def validate_artifacts(data: dict, base: Path) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != 2 or data.get("mode") != "live":
        raise ValueError("schema_version=2 and mode=live required; simulation is not live evidence")
    for key in ("approved_by", "authorization_reference", "verified_by"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{key} is required as a human attestation")
    for key in ("approved_at", "verified_at"):
        value = datetime.fromisoformat(str(data.get(key, "")))
        if value.tzinfo is None:
            raise ValueError(f"{key} must include timezone")
    if datetime.fromisoformat(data["approved_at"]) > datetime.fromisoformat(data["verified_at"]):
        raise ValueError("verification cannot precede approval")
    if not re.fullmatch(r"[1-9]\d*", str(data.get("kwork_order_id", ""))):
        raise ValueError("real numeric kwork_order_id required")
    attempt_id = uuid.UUID(str(data.get("send_attempt_id", "")))
    if attempt_id.int == 0:
        raise ValueError("nonzero attempt UUID required")
    if data.get("snapshot_verified") is not True or data.get("ui_matches_db") is not True:
        raise ValueError("snapshot_verified and ui_matches_db must be true human attestations")
    if not re.fullmatch(r"[a-f0-9]{64}", str(data.get("snapshot_sha256", ""))):
        raise ValueError("snapshot_sha256 required")
    _artifact(data.get("ui_evidence"), base)
    attempt = json.loads(_artifact(data.get("attempt_evidence"), base))
    if not isinstance(attempt, dict):
        raise TypeError("attempt evidence must be an object")
    for key in ("send_attempt_id", "kwork_order_id", "snapshot_sha256"):
        if attempt.get(key) != data.get(key):
            raise ValueError(f"attempt evidence mismatch: {key}")
    if (
        attempt.get("mode") != "live"
        or attempt.get("status") != "confirmed_sent"
        or attempt.get("dry_run") is not False
    ):
        raise ValueError("attempt evidence must describe a confirmed, non-simulated send")
    return data


async def verify_database(data: dict) -> None:
    from app.db.models import Job, SendAttempt
    from app.db.session import AsyncSessionLocal, engine
    from app.utils.formatting import extract_site_order_id

    try:
        async with AsyncSessionLocal() as session:
            attempt = await session.get(SendAttempt, uuid.UUID(data["send_attempt_id"]))
            if (
                not attempt
                or attempt.status != "confirmed_sent"
                or (attempt.platform_response or {}).get("dry_run") is True
            ):
                raise ValueError("DB attempt is missing, unconfirmed or simulated")
            if attempt.kind != "proposal" or not attempt.job_id:
                raise ValueError("live order acceptance requires a proposal attempt")
            job = await session.get(Job, attempt.job_id)
            if not job or job.platform != "kwork":
                raise ValueError("DB job must be a Kwork order")
            order_id = extract_site_order_id(
                external_id=job.external_id, url=job.url, platform=job.platform
            )
            if order_id != data["kwork_order_id"]:
                raise ValueError("DB order does not match signoff")
            digest = hashlib.sha256(attempt.snapshot_content.encode()).hexdigest()
            if digest != data["snapshot_sha256"]:
                raise ValueError("DB snapshot does not match evidence")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-only",
        action="store_true",
        help="Review only: does not verify DB or grant live acceptance",
    )
    args = parser.parse_args(argv)
    try:
        data = validate_artifacts(json.loads(SIGNOFF.read_text(encoding="utf-8")), SIGNOFF.parent)
        if not args.artifacts_only:
            asyncio.run(verify_database(data))
    except Exception as exc:
        # Do not expose connection errors, SQL parameters or artifact contents.
        print(
            f"Live evidence rejected ({type(exc).__name__}); check schema, artifacts and DB attempt.",
            file=sys.stderr,
        )
        return 1
    if args.artifacts_only:
        print("Artifacts valid; DB NOT checked. Review only, not live acceptance.")
    else:
        print(
            "Live artifacts and DB snapshot match. Human attestation still requires owner review."
        )
    print("No checklist items changed; no release approval granted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
