"""Milvus vector store (Milvus Lite for local deployment).

Collection: image_embeddings
- image_id (VARCHAR, PK, SHA-256) / embedding (FLOAT_VECTOR 768, COSINE)
- technical metadata + attribution + evaluation_metadata (JSON)
- FLAT index (no training, Milvus Lite friendly)
- idempotent upsert by image_id

`evaluation_metadata` is stored but NEVER used by search: it is excluded from
`output_fields` and from every filter/ranking expression in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol

from app.config import Settings
from app.errors import VectorStoreError
from app.logging_config import get_logger
from app.models.records import ImageReference, ImageSearchHit, ImageVectorRecord

logger = get_logger(__name__)

SEARCH_OUTPUT_FIELDS = [
    "image_id",
    "object_key",
    "content_type",
    "width",
    "height",
    "sha256",
    "source_url",
    "license",
    "creator",
]

_QUERY_CHUNK_SIZE = 200


class VectorStore(Protocol):
    async def ensure_ready(self) -> None: ...

    async def upsert(self, records: Sequence[ImageVectorRecord]) -> None: ...

    async def existing_created_at(self, image_ids: Sequence[str]) -> dict[str, int]: ...

    async def search(self, vector: Sequence[float], top_k: int) -> list[ImageSearchHit]: ...

    async def get_by_id(self, image_id: str) -> ImageReference | None: ...

    async def count(self) -> int: ...

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


class MilvusVectorStore:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client
        self._lock = asyncio.Lock()
        self._ready = False

    # ------------------------------------------------------------------ helpers
    @property
    def client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            kwargs: dict[str, Any] = {"uri": self._settings.milvus_uri}
            if self._settings.milvus_token:
                kwargs["token"] = self._settings.milvus_token
            self._client = MilvusClient(**kwargs)
        return self._client

    async def _run(self, fn, *args, **kwargs):
        async with self._lock:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except VectorStoreError:
                raise
            except Exception as exc:
                logger.exception("Milvus operation failed")
                raise VectorStoreError(
                    "Vector store operation failed", details={"reason": type(exc).__name__}
                ) from exc

    def _build_schema(self):
        from pymilvus import DataType, MilvusClient

        dim = self._settings.embedding_dimension
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("image_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("object_key", DataType.VARCHAR, max_length=1024)
        schema.add_field("content_type", DataType.VARCHAR, max_length=128)
        schema.add_field("width", DataType.INT32)
        schema.add_field("height", DataType.INT32)
        schema.add_field("sha256", DataType.VARCHAR, max_length=64)
        schema.add_field("source_url", DataType.VARCHAR, max_length=2048)
        schema.add_field("license", DataType.VARCHAR, max_length=128)
        schema.add_field("creator", DataType.VARCHAR, max_length=512)
        schema.add_field("evaluation_metadata", DataType.JSON)
        schema.add_field("created_at", DataType.INT64)
        return schema

    def _index_params(self):
        params = self.client.prepare_index_params()
        params.add_index(
            field_name="embedding",
            index_type=self._settings.milvus_index_type,
            metric_type=self._settings.milvus_metric_type,
        )
        return params

    def _create_collection_sync(self) -> None:
        client = self.client
        name = self._settings.milvus_collection
        if client.has_collection(name):
            self._verify_collection()
            return
        client.create_collection(
            collection_name=name,
            schema=self._build_schema(),
            index_params=self._index_params(),
        )
        logger.info("Created Milvus collection %s", name)

    def _verify_collection(self) -> None:
        """Fail fast when an existing collection has an incompatible vector dim."""
        client = self.client
        name = self._settings.milvus_collection
        try:
            description = client.describe_collection(name)
        except Exception:
            return
        for field_def in description.get("fields", []):
            params = field_def.get("params") or {}
            if field_def.get("name") == "embedding":
                dim = params.get("dim")
                if dim is not None and int(dim) != self._settings.embedding_dimension:
                    raise VectorStoreError(
                        "Existing Milvus collection embedding dimension mismatch",
                        details={
                            "expected": self._settings.embedding_dimension,
                            "found": int(dim),
                        },
                    )

    # ------------------------------------------------------------------ lifecycle
    async def ensure_ready(self) -> None:
        if self._ready:
            return
        await self._run(self._create_collection_sync)
        self._ready = True

    async def ping(self) -> None:
        def _ping() -> None:
            if not self.client.has_collection(self._settings.milvus_collection):
                raise VectorStoreError("Milvus collection is missing")

        await self._run(_ping)

    async def close(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            try:
                await asyncio.to_thread(self._client.close)
            except Exception:
                logger.warning("Failed to close Milvus client", exc_info=True)
            self._client = None
            self._ready = False

    # ------------------------------------------------------------------ writes
    def _upsert_sync(self, rows: list[dict[str, Any]]) -> None:
        self._create_collection_sync()
        self.client.upsert(collection_name=self._settings.milvus_collection, data=rows)

    async def upsert(self, records: Sequence[ImageVectorRecord]) -> None:
        if not records:
            return
        rows: list[dict[str, Any]] = []
        for record in records:
            if len(record.embedding) != self._settings.embedding_dimension:
                raise VectorStoreError(
                    "Embedding dimension mismatch before upsert",
                    details={
                        "expected": self._settings.embedding_dimension,
                        "found": len(record.embedding),
                    },
                )
            rows.append(
                {
                    "image_id": record.image_id,
                    "embedding": list(record.embedding),
                    "object_key": record.object_key,
                    "content_type": record.content_type,
                    "width": int(record.width),
                    "height": int(record.height),
                    "sha256": record.sha256,
                    "source_url": record.source_url,
                    "license": record.license,
                    "creator": record.creator,
                    "evaluation_metadata": record.evaluation_metadata or {},
                    "created_at": int(record.created_at),
                }
            )
        await self._run(self._upsert_sync, rows)

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _id_filter(image_ids: Sequence[str]) -> str:
        import json

        return f"image_id in {json.dumps(list(image_ids))}"

    async def existing_created_at(self, image_ids: Sequence[str]) -> dict[str, int]:
        ids = [image_id for image_id in dict.fromkeys(image_ids) if image_id]
        if not ids:
            return {}

        def _query() -> dict[str, int]:
            self._create_collection_sync()
            found: dict[str, int] = {}
            for start in range(0, len(ids), _QUERY_CHUNK_SIZE):
                chunk = ids[start : start + _QUERY_CHUNK_SIZE]
                rows = self.client.query(
                    collection_name=self._settings.milvus_collection,
                    filter=self._id_filter(chunk),
                    output_fields=["image_id", "created_at"],
                )
                for row in rows:
                    image_id = row.get("image_id")
                    created_at = row.get("created_at")
                    if image_id and created_at is not None:
                        found[str(image_id)] = int(created_at)
            return found

        return await self._run(_query)

    @staticmethod
    def _hit_from_row(row: dict[str, Any], score: float) -> ImageSearchHit:
        return ImageSearchHit(
            image_id=str(row.get("image_id") or row.get("id") or ""),
            score=float(score),
            object_key=str(row.get("object_key") or ""),
            content_type=str(row.get("content_type") or "application/octet-stream"),
            width=int(row.get("width") or 0),
            height=int(row.get("height") or 0),
            sha256=str(row.get("sha256") or ""),
            source_url=str(row.get("source_url") or ""),
            license=str(row.get("license") or ""),
            creator=str(row.get("creator") or ""),
        )

    async def search(self, vector: Sequence[float], top_k: int) -> list[ImageSearchHit]:
        values = list(vector)
        if len(values) != self._settings.embedding_dimension:
            raise VectorStoreError("Query embedding dimension mismatch")

        def _search() -> list[ImageSearchHit]:
            self._create_collection_sync()
            results = self.client.search(
                collection_name=self._settings.milvus_collection,
                data=[values],
                anns_field="embedding",
                limit=top_k,
                output_fields=SEARCH_OUTPUT_FIELDS,
                search_params={
                    "metric_type": self._settings.milvus_metric_type,
                    "params": {},
                },
            )
            hits: list[ImageSearchHit] = []
            if not results:
                return hits
            for raw in results[0]:
                entity = raw.get("entity") or {}
                row = {**entity, "image_id": entity.get("image_id", raw.get("id"))}
                hits.append(self._hit_from_row(row, float(raw.get("distance", 0.0))))
            return hits

        return await self._run(_search)

    async def get_by_id(self, image_id: str) -> ImageReference | None:
        def _query() -> ImageReference | None:
            self._create_collection_sync()
            rows = self.client.query(
                collection_name=self._settings.milvus_collection,
                filter=f'image_id == "{image_id}"',
                output_fields=["image_id", "object_key", "content_type", "sha256"],
            )
            if not rows:
                return None
            row = rows[0]
            return ImageReference(
                image_id=str(row.get("image_id") or image_id),
                object_key=str(row.get("object_key") or ""),
                content_type=str(row.get("content_type") or "application/octet-stream"),
                sha256=str(row.get("sha256") or ""),
            )

        return await self._run(_query)

    async def count(self) -> int:
        def _count() -> int:
            self._create_collection_sync()
            rows = self.client.query(
                collection_name=self._settings.milvus_collection,
                filter="",
                output_fields=["count(*)"],
            )
            if not rows:
                return 0
            return int(rows[0].get("count(*)", 0))

        return await self._run(_count)
