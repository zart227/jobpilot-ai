"""Embedding provider independent from chat/scoring LLM."""

from __future__ import annotations

import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """Semantic search must be skipped when embeddings cannot be produced."""


async def embed_text(text: str, settings: Settings | None = None) -> list[float]:
    settings = settings or get_settings()
    provider = (settings.embedding_provider or "openai").strip().lower()
    model = settings.embedding_model

    if provider == "openai":
        if not settings.openai_api_key:
            raise EmbeddingUnavailableError("OPENAI_API_KEY required for embeddings")
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"input": text, "model": model},
            )
            response.raise_for_status()
            data = response.json()["data"][0]["embedding"]
            return list(data)

    raise EmbeddingUnavailableError(f"unsupported embedding provider: {provider}")
