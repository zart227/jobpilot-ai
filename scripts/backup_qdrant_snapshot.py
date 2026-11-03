"""Create a Qdrant full-storage snapshot (read-only backup step)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)
ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "data" / "backups"


async def create_snapshot() -> dict:
    settings = get_settings()
    url = settings.qdrant_url.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(f"{url}/snapshots")
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or payload
        name = result.get("name") or result.get("snapshot_name")
        if not name:
            list_resp = await client.get(f"{url}/snapshots")
            list_resp.raise_for_status()
            snapshots = list_resp.json().get("result", [])
            name = snapshots[-1]["name"] if snapshots else None
        return {"snapshot_name": name, "raw": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-meta",
        type=Path,
        default=None,
        help="Write JSON metadata beside data/backups (default: auto timestamp)",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(create_snapshot())
    except Exception as exc:
        logger.error("Qdrant snapshot failed", error=str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    meta_path = args.write_meta or BACKUP_DIR / f"qdrant_snapshot_{stamp}.json"
    meta = {
        "ok": True,
        "created_at": datetime.now(UTC).isoformat(),
        "qdrant_host": get_settings().qdrant_url.split("@")[-1],
        **result,
        "notes": "Snapshot files remain in Qdrant storage volume; use Qdrant recovery docs to restore.",
    }
    body = json.dumps(meta, ensure_ascii=False, indent=2)
    meta_path.write_text(body, encoding="utf-8")
    digest_path = Path(str(meta_path) + ".sha256")
    digest_path.write_text(hashlib.sha256(body.encode()).hexdigest() + "\n", encoding="utf-8")
    print(body)
    print(f"Metadata written to {meta_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
