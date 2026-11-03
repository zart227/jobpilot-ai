"""Embedding index signature for staged reindex when model/dim changes."""

from __future__ import annotations

from app.config import Settings, get_settings

SIGNATURE_KEY = "embedding_index_signature"
LEGACY_INDEXED_WITHOUT_SIGNATURE = "legacy_indexed_without_signature"


def embedding_index_signature(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    return f"{cfg.embedding_provider}:{cfg.embedding_model}:{cfg.embedding_dimensions}"


def expected_job_embedding_collection(settings: Settings | None = None) -> str:
    from app.memory.qdrant_store import QdrantMemoryStore

    cfg = settings or get_settings()
    store = object.__new__(QdrantMemoryStore)
    store._settings = cfg
    store._prefix = cfg.qdrant_collection_prefix
    store._vector_dim = cfg.embedding_dimensions
    return store._collection_name("job_embeddings")


def job_has_legacy_missing_signature(raw_data: dict | None) -> bool:
    """Job likely indexed before signature tracking (not pending, no signature key)."""
    raw = raw_data or {}
    if raw.get("embedding_pending") in (True, "true"):
        return False
    return not raw.get(SIGNATURE_KEY)


def job_needs_embedding_reindex(
    raw_data: dict | None,
    settings: Settings | None = None,
    *,
    include_legacy_unsigned: bool = False,
) -> bool:
    raw = raw_data or {}
    if raw.get("embedding_pending") in (True, "true"):
        return True
    stored = raw.get(SIGNATURE_KEY)
    if not stored:
        return include_legacy_unsigned and job_has_legacy_missing_signature(raw)
    return stored != embedding_index_signature(settings)
