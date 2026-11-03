"""Embedding index signature drives staged reindex on config change."""


from app.services.embedding_index_signature import (
    SIGNATURE_KEY,
    embedding_index_signature,
    job_needs_embedding_reindex,
)


def test_signature_changes_when_dimensions_change():
    from app.config import Settings

    a = Settings(_env_file=None, embedding_dimensions=1536)
    b = Settings(_env_file=None, embedding_dimensions=512)
    assert embedding_index_signature(a) != embedding_index_signature(b)


def test_job_needs_reindex_when_signature_mismatch():
    current = embedding_index_signature()
    assert job_needs_embedding_reindex({SIGNATURE_KEY: "old:model:512"}) is True
    assert job_needs_embedding_reindex({SIGNATURE_KEY: current}) is False
    assert job_needs_embedding_reindex({"embedding_pending": True}) is True


def test_legacy_unsigned_excluded_by_default():
    assert job_needs_embedding_reindex({}) is False
    assert job_needs_embedding_reindex({}, include_legacy_unsigned=True) is True
