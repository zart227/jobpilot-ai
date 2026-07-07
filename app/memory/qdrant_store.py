import hashlib
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

logger = structlog.get_logger(__name__)

COLLECTIONS = {
    "successful_proposals": 1536,
    "high_conversion_phrases": 1536,
    "job_embeddings": 1536,
    "edit_preferences": 1536,
}


class QdrantMemoryStore:
    """JobPilot AI vector memory backed by Qdrant."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = QdrantClient(url=self._settings.qdrant_url, check_compatibility=False)
        self._prefix = self._settings.qdrant_collection_prefix
        self._ensure_collections()

    def _collection_name(self, name: str) -> str:
        return f"{self._prefix}_{name}"

    def _ensure_collections(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        for name, dim in COLLECTIONS.items():
            full_name = self._collection_name(name)
            if full_name not in existing:
                self._client.create_collection(
                    collection_name=full_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                logger.info("JobPilot AI created Qdrant collection", collection=full_name)

    async def _embed(self, text: str) -> list[float]:
        if self._settings.llm_provider == "openai" and self._settings.openai_api_key:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                    json={"input": text, "model": "text-embedding-3-small"},
                )
                response.raise_for_status()
                return response.json()["data"][0]["embedding"]
        return self._hash_embedding(text)

    def _hash_embedding(self, text: str, dim: int = 1536) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        values: list[float] = []
        for i in range(dim):
            byte = digest[i % len(digest)]
            values.append((byte / 255.0) * 2 - 1)
        return values

    def _point_id(self, key: str) -> int:
        return int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)

    async def store_successful_proposal(
        self,
        proposal_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        vector = await self._embed(content)
        point = PointStruct(
            id=self._point_id(proposal_id),
            vector=vector,
            payload={"content": content, "proposal_id": proposal_id, "type": "successful_proposal", **(metadata or {})},
        )
        self._client.upsert(
            collection_name=self._collection_name("successful_proposals"),
            points=[point],
        )

    async def store_pricing_phrase(self, phrase: str, score: float) -> None:
        vector = await self._embed(phrase)
        point_id = int(hashlib.md5(phrase.encode()).hexdigest()[:8], 16)
        point = PointStruct(
            id=point_id,
            vector=vector,
            payload={"phrase": phrase, "conversion_score": score, "type": "phrase"},
        )
        self._client.upsert(
            collection_name=self._collection_name("high_conversion_phrases"),
            points=[point],
        )

    async def store_job_embedding(self, job_id: str, job_text: str, metadata: dict[str, Any]) -> None:
        vector = await self._embed(job_text)
        point = PointStruct(
            id=self._point_id(job_id),
            vector=vector,
            payload={"job_id": job_id, "job_text": job_text[:500], **metadata},
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
        vector = await self._embed(query)
        results = self._vector_search(
            self._collection_name("successful_proposals"),
            vector,
            limit,
        )
        return [{"score": hit.score, **hit.payload} for hit in results if hit.payload]

    async def search_phrases(self, query: str, limit: int = 5) -> list[str]:
        vector = await self._embed(query)
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
        vector = await self._embed(f"{instruction}\n{original_content[:300]}")
        point = PointStruct(
            id=self._point_id(key),
            vector=vector,
            payload={
                "instruction": instruction,
                "original_snippet": original_content[:500],
                "edited_snippet": edited_content[:500],
                "type": "edit_preference",
                **(metadata or {}),
            },
        )
        self._client.upsert(
            collection_name=self._collection_name("edit_preferences"),
            points=[point],
        )

    async def search_edit_preferences(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        vector = await self._embed(query)
        results = self._vector_search(
            self._collection_name("edit_preferences"),
            vector,
            limit,
        )
        return [{"score": hit.score, **hit.payload} for hit in results if hit.payload]

    async def find_similar_jobs(self, job_text: str, platform: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        vector = await self._embed(job_text)
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
