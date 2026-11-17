"""Milvus Lite integration-style test (skipped when the local engine is unusable)."""

from __future__ import annotations

import uuid

import pytest
from app.integrations.vector_store import MilvusVectorStore
from app.models.records import ImageVectorRecord
from tests.support import make_settings


def _record(image_id: str, vector: list[float], **overrides) -> ImageVectorRecord:
    values = {
        "image_id": image_id,
        "embedding": vector,
        "object_key": f"images/bank-mas/{image_id}.png",
        "content_type": "image/png",
        "width": 16,
        "height": 12,
        "sha256": image_id,
        "source_url": "https://example.test/source",
        "license": "cc-by",
        "creator": "Creator",
        "evaluation_metadata": {
            "title": "SECRET TITLE",
            "tags": ["secret"],
            "requestedClass": "dog",
        },
        "created_at": 1700000000,
    }
    values.update(overrides)
    return ImageVectorRecord(**values)


async def test_milvus_lite_upsert_search_and_idempotency(tmp_path) -> None:
    pytest.importorskip("pymilvus")
    settings = make_settings(
        milvus_uri=str(tmp_path / "milvus.db"),
        milvus_collection=f"image_embeddings_{uuid.uuid4().hex[:8]}",
    )
    store = MilvusVectorStore(settings)
    try:
        await store.ensure_ready()
    except Exception as exc:
        message = str(exc).lower()
        if isinstance(exc, OSError | ImportError) or "milvus" in message:
            pytest.skip(f"Milvus Lite unavailable in this environment: {exc}")
        raise

    vector_a = [0.0] * 768
    vector_a[0] = 1.0
    vector_b = [0.0] * 768
    vector_b[1] = 1.0
    image_a = "a" * 64
    image_b = "b" * 64

    try:
        await store.upsert([_record(image_a, vector_a), _record(image_b, vector_b)])
        assert await store.count() == 2

        # Idempotent upsert: same primary keys must not create new rows.
        await store.upsert([_record(image_a, vector_a)])
        assert await store.count() == 2

        hits = await store.search(vector_a, top_k=2)
        assert hits[0].image_id == image_a
        assert hits[0].score == pytest.approx(1.0, abs=1e-4)
        assert hits[0].object_key == f"images/bank-mas/{image_a}.png"
        assert not hasattr(hits[0], "evaluation_metadata")

        reference = await store.get_by_id(image_a)
        assert reference is not None
        assert reference.object_key == f"images/bank-mas/{image_a}.png"

        # evaluation_metadata is stored verbatim in the same row (offline evaluation only).
        rows = store.client.query(
            collection_name=settings.milvus_collection,
            filter=f'image_id == "{image_a}"',
            output_fields=["evaluation_metadata", "created_at"],
        )
        assert rows[0]["evaluation_metadata"]["title"] == "SECRET TITLE"
        assert rows[0]["created_at"] == 1700000000
    finally:
        await store.close()
