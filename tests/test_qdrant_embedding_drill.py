"""Unit tests for qdrant embedding drill helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _load_drill_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "qdrant_embedding_drill.py"
    spec = importlib.util.spec_from_file_location("qdrant_embedding_drill", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_probe_qdrant_ok() -> None:
    drill = _load_drill_module()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "result": {
                    "collections": [
                        {"name": "jobpilot_ai_job_embeddings"},
                        {"name": "other"},
                    ]
                }
            }

    with patch.object(drill, "httpx") as httpx_mod:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get.return_value = FakeResponse()
        httpx_mod.AsyncClient.return_value = client

        with patch.object(drill, "get_settings") as settings:
            settings.return_value.qdrant_url = "http://localhost:6333"
            settings.return_value.qdrant_collection_prefix = "jobpilot_ai"
            result = await drill._probe_qdrant()

    assert result["ok"] is True
    assert result["collections_matching_prefix"] == ["jobpilot_ai_job_embeddings"]


@pytest.mark.asyncio
async def test_count_embedding_pending_zero() -> None:
    drill = _load_drill_module()

    with patch.object(drill, "AsyncSessionLocal") as session_factory:
        session = AsyncMock()
        session_factory.return_value.__aenter__.return_value = session
        session_factory.return_value.__aexit__.return_value = None
        execute_result = MagicMock()
        execute_result.scalar.return_value = 0
        session.execute = AsyncMock(return_value=execute_result)

        count = await drill._count_embedding_pending()

    assert count == 0
