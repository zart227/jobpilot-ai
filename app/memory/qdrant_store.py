import hashlib
import re
from typing import Any

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.config import Settings, get_settings
from app.llm.embeddings import EmbeddingUnavailableError, embed_text

logger = structlog.get_logger(__name__)

COLLECTION_BASES = (
    "successful_proposals",
    "high_conversion_phrases",
    "job_embeddings",
    "edit_preferences",
)


class QdrantMemoryStore:
    """JobPilot AI vector memory backed by Qdrant."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = QdrantClient(url=self._settings.qdrant_url, check_compatibility=False)
        self._prefix = self._settings.qdrant_collection_prefix
        self._vector_dim = self._settings.embedding_dimensions
        self._ensure_collections()

    def _model_slug(self) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", self._settings.embedding_model)
        return slug.strip("_") or "default"

    def _collection_name(self, name: str) -> str:
        return f"{self._prefix}_{name}_{self._model_slug()}"

    def _ensure_collections(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        for name in COLLECTION_BASES:
            full_name = self._collection_name(name)
            if full_name not in existing:
                self._client.create_collection(
                    collection_name=full_name,
                    vectors_config=VectorParams(size=self._vector_dim, distance=Distance.COSINE),
                )
                logger.info(
                    "JobPilot AI created Qdrant collection",
                    collection=full_name,
                    model=self._settings.embedding_model,
                    dimensions=self._vector_dim,
                )

    async def _embed(self, text: str) -> list[float]:
        vector = await embed_text(text, self._settings)
        if len(vector) != self._vector_dim:
            raise EmbeddingUnavailableError(
                f"embedding dimension {len(vector)} != configured {self._vector_dim}"
            )
        return vector

    def _point_id(self, key: str) -> int:
        return int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)

    async def store_successful_proposal(
        self,
        proposal_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            vector = await self._embed(content)
        except EmbeddingUnavailableError as exc:
            logger.warning("Skipping successful proposal embedding", error=str(exc))
            return
        point = PointStruct(
            id=self._point_id(proposal_id),
            vector=vector,
            payload={
                "content": content,
                "proposal_id": proposal_id,
                "type": "successful_proposal",
                "embedding_model": self._settings.embedding_model,
                **(metadata or {}),
            },
        )
        self._client.upsert(
            collection_name=self._collection_name("successful_proposals"),
            points=[point],
        )

    async def store_pricing_phrase(self, phrase: str, score: float) -> None:
        try:
            vector = await self._embed(phrase)
        except EmbeddingUnavailableError as exc:
            logger.warning("Skipping phrase embedding", error=str(exc))
            return
        point_id = int(hashlib.md5(phrase.encode()).hexdigest()[:8], 16)
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "phrase": phrase,
                "conversion_score": score,
                "type": "phrase",
                "embedding_model": self._settings.embedding_model,
            },
        )
        self._client.upsert(
            collection_name=self._collection_name("high_conversion_phrases"),
            points=[point],
        )

    async def store_job_embedding(self, job_id: str, job_text: str, metadata: dict[str, Any]) -> None:
        try:
            vector = await self._embed(job_text)
        except EmbeddingUnavailableError as exc:
            logger.warning("Skipping job embedding", error=str(exc))
            return
        point = PointStruct(
            id=self._point_id(job_id),
            vector=vector,
            payload={
                "job_id": job_id,
                "job_text": job_text[:500],
                "embedding_model": self._settings.embedding_model,
                **metadata,
            },
        )
        self._client.upsert(
            collection_name=self._collection_name("job_embeddings"),
            points=[point],
        )

    def _vector_search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        query_filter: Filter | None = None,
    ) -> list[Any]:
        if hasattr(self._client, "query_points"):
            response = self._client.query_points(
                collection_name=collection,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            return list(response.points)
        return self._client.search(
            collection_name=collection,
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
        )

    async def search_similar_proposals(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        try:
            vector = await self._embed(query)
        except EmbeddingUnavailableError as exc:
            logger.warning("Semantic proposal search disabled", error=str(exc))
            return []
        results = self._vector_search(
            self._collection_name("successful_proposals"),
            vector,
            limit,
        )
        return [{"score": hit.score, **hit.payload} for hit in results if hit.payload]

    async def search_phrases(self, query: str, limit: int = 5) -> list[str]:
        try:
            vector = await self._embed(query)
        except EmbeddingUnavailableError as exc:
            logger.warning("Semantic phrase search disabled", error=str(exc))
            return []
        results = self._vector_search(
            self._collection_name("high_conversion_phrases"),
            vector,
            limit,
        )
        return [str(hit.payload.get("phrase", "")) for hit in results if hit.payload]

    async def store_edit_preference(
        self,
        instruction: str,
        original_content: str,
        edited_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = f"{instruction}:{original_content[:120]}:{edited_content[:120]}"
        try:
            vector = await self._embed(f"{instruction}\n{original_content[:300]}")
        except EmbeddingUnavailableError as exc:
            logger.warning("Skipping edit preference embedding", error=str(exc))
            return
        point = PointStruct(
            id=self._point_id(key),
            vector=vector,
            payload={
                "instruction": instruction,
                "original_snippet": original_content[:500],
                "edited_snippet": edited_content[:500],
                "type": "edit_preference",
                "embedding_model": self._settings.embedding_model,
                **(metadata or {}),
            },
        )
        self._client.upsert(
            collection_name=self._collection_name("edit_preferences"),
            points=[point],
        )

    async def search_edit_preferences(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            vector = await self._embed(query)
        except EmbeddingUnavailableError as exc:
            logger.warning("Semantic edit preference search disabled", error=str(exc))
            return []
        results = self._vector_search(
            self._collection_name("edit_preferences"),
            vector,
            limit,
        )
        return [{"score": hit.score, **hit.payload} for hit in results if hit.payload]

    async def find_similar_jobs(self, job_text: str, platform: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        try:
            vector = await self._embed(job_text)
        except EmbeddingUnavailableError as exc:
            logger.warning("Semantic job search disabled", error=str(exc))
            return []
        query_filter = None
        if platform:
            query_filter = Filter(
                must=[FieldCondition(key="platform", match=MatchValue(value=platform))]
            )
        results = self._vector_search(
            self._collection_name("job_embeddings"),
            vector,
            limit,
            query_filter=query_filter,
        )
        return [{"score": hit.score, **hit.payload} for hit in results if hit.payload]


def get_memory_store(settings: Settings | None = None) -> QdrantMemoryStore:
    return QdrantMemoryStore(settings)
