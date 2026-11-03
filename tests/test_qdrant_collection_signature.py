"""Qdrant collection names must include embedding dimension (C089 partial)."""

from app.config import Settings
from app.memory.qdrant_store import QdrantMemoryStore


def test_different_dimensions_use_distinct_collection_names():
    names = []
    for dim in (1536, 512):
        store = object.__new__(QdrantMemoryStore)
        store._settings = Settings(
            _env_file=None,
            embedding_provider="openai",
            embedding_model="same-model",
            embedding_dimensions=dim,
        )
        store._prefix = "jobpilot"
        store._vector_dim = dim
        names.append(store._collection_name("job_embeddings"))
    assert names[0] != names[1]
    assert names[0].endswith("_d1536")
    assert names[1].endswith("_d512")
    assert "_openai_" in names[0]


def test_different_providers_use_distinct_collection_names():
    from app.config import Settings
    from app.memory.qdrant_store import QdrantMemoryStore

    a = object.__new__(QdrantMemoryStore)
    a._settings = Settings(_env_file=None, embedding_provider="openai", embedding_model="m", embedding_dimensions=1536)
    a._prefix = "p"
    a._vector_dim = 1536
    b = object.__new__(QdrantMemoryStore)
    b._settings = Settings(_env_file=None, embedding_provider="ollama", embedding_model="m", embedding_dimensions=1536)
    b._prefix = "p"
    b._vector_dim = 1536
    assert a._collection_name("job_embeddings") != b._collection_name("job_embeddings")
