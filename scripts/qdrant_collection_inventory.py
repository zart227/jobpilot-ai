"""Read-only Qdrant collection inventory: legacy vs canonical names and point counts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.memory.qdrant_store import COLLECTION_BASES, QdrantMemoryStore

logger = structlog.get_logger(__name__)


def _expected_names(settings) -> dict[str, str]:
    store = object.__new__(QdrantMemoryStore)
    store._settings = settings
    store._prefix = settings.qdrant_collection_prefix
    store._vector_dim = settings.embedding_dimensions
    return {base: store._collection_name(base) for base in COLLECTION_BASES}


async def _fetch_collections(url: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{url.rstrip('/')}/collections")
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("result", {}).get("collections", []))


async def _collection_detail(url: str, name: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{url.rstrip('/')}/collections/{name}")
        if response.status_code == 404:
            return {"name": name, "missing": True}
        response.raise_for_status()
        result = response.json().get("result", {})
        return {
            "name": name,
            "points_count": result.get("points_count"),
            "vectors_count": result.get("vectors_count"),
            "status": result.get("status"),
            "config": {
                "size": result.get("config", {})
                .get("params", {})
                .get("vectors", {})
                .get("size"),
            },
        }


def _classify(name: str, prefix: str, expected: set[str]) -> str:
    if name in expected:
        return "canonical"
    if name.startswith(f"{prefix}_"):
        return "legacy"
    return "other"


async def run_inventory() -> dict[str, Any]:
    settings = get_settings()
    url = settings.qdrant_url
    prefix = settings.qdrant_collection_prefix
    expected_map = _expected_names(settings)
    expected_set = set(expected_map.values())

    try:
        listed = await _fetch_collections(url)
    except Exception as exc:
        logger.warning("Qdrant list failed", error=str(exc))
        return {"ok": False, "error": str(exc)}

    names = sorted({item.get("name") for item in listed if item.get("name")})
    prefix_names = [n for n in names if n.startswith(f"{prefix}_")]

    details: list[dict[str, Any]] = []
    for name in prefix_names:
        detail = await _collection_detail(url, name)
        detail["class"] = _classify(name, prefix, expected_set)
        detail["collection_base"] = _guess_base(name, prefix)
        details.append(detail)

    canonical = [d for d in details if d["class"] == "canonical"]
    legacy = [d for d in details if d["class"] == "legacy"]

    return {
        "ok": True,
        "qdrant_url": url.split("@")[-1] if "@" in url else url,
        "embedding_config": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
            "signature": f"{settings.embedding_provider}:{settings.embedding_model}:{settings.embedding_dimensions}",
        },
        "expected_collections": expected_map,
        "summary": {
            "prefix_collections": len(prefix_names),
            "canonical_count": len(canonical),
            "legacy_count": len(legacy),
            "canonical_points_total": sum(int(d.get("points_count") or 0) for d in canonical),
            "legacy_points_total": sum(int(d.get("points_count") or 0) for d in legacy),
        },
        "collections": details,
        "notes": [
            "Do not delete legacy collections until search verification against canonical collections.",
            "Reindex job vectors from PostgreSQL jobs, not by copying Qdrant points across collections.",
        ],
    }


def _guess_base(name: str, prefix: str) -> str | None:
    rest = name[len(prefix) + 1 :] if name.startswith(f"{prefix}_") else name
    for base in COLLECTION_BASES:
        if rest == base or rest.startswith(f"{base}_"):
            return base
    return None


def main() -> int:
    try:
        report = asyncio.run(run_inventory())
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not report.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
