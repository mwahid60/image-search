"""Vector-only search: query text -> embedding -> Milvus top-k -> attribution metadata.

No Openverse/Java calls. `evaluation_metadata` is never selected from Milvus and
never returned to the client.
"""

from __future__ import annotations

from pydantic import Field

from app.config import Settings
from app.errors import InvalidRequestError
from app.integrations.embeddings import EmbeddingsClient
from app.integrations.vector_store import VectorStore
from app.models.manifest import CamelModel
from app.models.records import ImageSearchHit
from app.utils import l2_normalize


class SearchResult(CamelModel):
    image_id: str
    score: float
    image_url: str
    object_key: str
    content_type: str
    source_url: str = ""
    license: str = ""
    creator: str = ""
    width: int = 0
    height: int = 0


class SearchResponse(CamelModel):
    query: str
    results: list[SearchResult] = Field(default_factory=list)


class SearchService:
    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
    ) -> None:
        self._settings = settings
        self._embeddings = embeddings
        self._vectors = vector_store

    async def search(self, query: str, top_k: int | None = None) -> SearchResponse:
        cleaned = (query or "").strip()
        if not cleaned:
            raise InvalidRequestError("query must not be empty", code="INVALID_QUERY")
        if len(cleaned) > self._settings.search_max_query_length:
            raise InvalidRequestError(
                f"query must be at most {self._settings.search_max_query_length} characters",
                code="QUERY_TOO_LONG",
            )
        limit = self._settings.search_default_top_k if top_k is None else top_k
        if limit < 1 or limit > self._settings.search_max_top_k:
            raise InvalidRequestError(
                f"topK must be between 1 and {self._settings.search_max_top_k}",
                code="INVALID_TOP_K",
            )

        raw_vector = await self._embeddings.embed_text(cleaned)
        vector = l2_normalize(raw_vector, dimension=self._settings.embedding_dimension)
        hits = await self._vectors.search(vector, limit)
        return SearchResponse(
            query=cleaned,
            results=[self._to_result(hit) for hit in hits],
        )

    def _to_result(self, hit: ImageSearchHit) -> SearchResult:
        return SearchResult(
            image_id=hit.image_id,
            score=float(hit.score),
            image_url=f"/api/v1/images/{hit.image_id}",
            object_key=hit.object_key,
            content_type=hit.content_type,
            source_url=hit.source_url,
            license=hit.license,
            creator=hit.creator,
            width=hit.width,
            height=hit.height,
        )
