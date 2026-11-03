"""C089: staged pilot reindex + search smoke on isolated test DB and live Qdrant (fake embed)."""

from __future__ import annotations

import hashlib
import os
import uuid

import httpx
import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
        reason="isolated DB required",
    ),
    pytest.mark.skipif(
        os.environ.get("JOBPILOT_QDRANT_DRILL") != "1",
        reason="set JOBPILOT_QDRANT_DRILL=1 to run staged reindex against compose Qdrant",
    ),
]


def _deterministic_vector(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    out: list[float] = []
    while len(out) < dim:
        for byte in digest:
            out.append((byte / 255.0) * 2.0 - 1.0)
            if len(out) >= dim:
                break
        digest = hashlib.sha256(digest).digest()
    return out


async def _qdrant_collection_names(url: str) -> set[str]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{url.rstrip('/')}/collections")
        response.raise_for_status()
        payload = response.json()
        return {
            item.get("name")
            for item in payload.get("result", {}).get("collections", [])
            if item.get("name")
        }


@pytest.mark.asyncio
async def test_staged_pilot_reindex_search_smoke_preserves_legacy_collections(monkeypatch):
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.llm import embeddings
    from app.services.embedding_index_signature import SIGNATURE_KEY, embedding_index_signature
    from scripts import pilot_embedding_reindex as pilot_mod
    from scripts import verify_pilot_embedding_search as verify_mod

    settings = get_settings()
    qdrant_url = settings.qdrant_url
    try:
        before_names = await _qdrant_collection_names(qdrant_url)
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable: {exc}")

    dim = settings.embedding_dimensions

    async def fake_embed(text: str, _settings):
        return _deterministic_vector(text, dim)

    monkeypatch.setattr(embeddings, "embed_text", fake_embed)
    monkeypatch.setattr("app.memory.qdrant_store.embed_text", fake_embed)

    async with AsyncSessionLocal() as session:
        pending_rows = list(
            (
                await session.scalars(
                    select(Job).where(Job.raw_data["embedding_pending"].as_boolean().is_(True))
                )
            ).all()
        )
        for row in pending_rows:
            raw = dict(row.raw_data or {})
            raw["embedding_pending"] = False
            row.raw_data = raw
        if pending_rows:
            await session.commit()

    job_ids: list[uuid.UUID] = []
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        for index in range(3):
            job_id = uuid.uuid4()
            job_ids.append(job_id)
            session.add(
                Job(
                    id=job_id,
                    platform="kwork",
                    external_id=f"c089-{suffix}-{index}",
                    title=f"C089 pilot title {suffix} {index}",
                    description=f"Unique staged reindex body {suffix} number {index} for search smoke.",
                    status="new",
                    raw_data={},
                )
            )
        await session.commit()

    signature = embedding_index_signature(settings)
    report = await pilot_mod.run_pilot(
        limit=3,
        job_ids=[str(j) for j in job_ids],
        include_legacy_unsigned=True,
        apply=True,
        confirm=signature,
        execute=True,
    )
    assert report.get("marked_pending") == 3
    assert report.get("reindex_result", {}).get("indexed", 0) >= 3

    async with AsyncSessionLocal() as session:
        for job_id in job_ids:
            row = await session.get(Job, job_id)
            assert row is not None
            assert row.raw_data.get(SIGNATURE_KEY) == signature
            assert row.raw_data.get("embedding_pending") is False

    verify = await verify_mod.verify([str(j) for j in job_ids], limit=3)
    assert verify.get("ok") is True
    assert verify.get("checked") == 3
    assert verify.get("summary", {}).get("self_in_top10_failed") == 0

    after_names = await _qdrant_collection_names(qdrant_url)
    assert before_names.issubset(after_names), "legacy Qdrant collections must not be removed by pilot batch"
