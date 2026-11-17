"""Search service tests: vector-only ranking and strict metadata whitelist."""

from __future__ import annotations

import json
import math

import pytest
from app.errors import InvalidRequestError
from app.models.records import ImageVectorRecord
from app.services.search import SearchService
from app.utils import l2_normalize
from tests.support import FakeEmbeddings, FakeVectorStore, make_settings


def record(image_id: str, seed: float, metadata_title: str = "SECRET TITLE") -> ImageVectorRecord:
    vector = l2_normalize([seed, 1.0 - seed] + [0.0] * 766)
    return ImageVectorRecord(
        image_id=image_id,
        embedding=vector,
        object_key=f"images/bank-mas/{image_id}.png",
        content_type="image/png",
        width=640,
        height=480,
        sha256=image_id,
        source_url="https://example.test/source",
        license="cc-by-sa",
        creator="Creator",
        evaluation_metadata={
            "title": metadata_title,
            "tags": ["dog", "snow"],
            "requestedClass": "dog",
        },
        created_at=1,
    )


def build() -> tuple[SearchService, FakeEmbeddings, FakeVectorStore]:
    settings = make_settings(search_default_top_k=3, search_max_top_k=5)
    embeddings = FakeEmbeddings()
    vectors = FakeVectorStore()
    vectors.records["a" * 64] = record("a" * 64, 0.9)
    vectors.records["b" * 64] = record("b" * 64, 0.1)
    return SearchService(settings, embeddings, vectors), embeddings, vectors


async def test_search_returns_vector_hits_with_attribution_only() -> None:
    service, embeddings, vectors = build()
    response = await service.search("a dog playing in the snow", top_k=2)
    body = response.model_dump(by_alias=True, mode="json")

    assert body["query"] == "a dog playing in the snow"
    assert len(body["results"]) == 2
    first = body["results"][0]
    assert set(first) == {
        "imageId",
        "score",
        "imageUrl",
        "objectKey",
        "contentType",
        "sourceUrl",
        "license",
        "creator",
        "width",
        "height",
    }
    assert first["imageUrl"] == f"/api/v1/images/{first['imageId']}"
    assert first["objectKey"].startswith("images/bank-mas/")
    assert "SECRET TITLE" not in json.dumps(body)
    assert "evaluationMetadata" not in json.dumps(body)
    assert "tags" not in json.dumps(body)
    assert 0.0 <= first["score"] <= 1.0
    assert first["score"] >= body["results"][1]["score"]

    assert embeddings.text_calls == ["a dog playing in the snow"]
    sent_vector, limit = vectors.search_calls[0]
    assert limit == 2
    assert math.isclose(math.sqrt(sum(value * value for value in sent_vector)), 1.0)


async def test_default_top_k_is_used() -> None:
    service, _, vectors = build()
    await service.search("dog")
    assert vectors.search_calls[0][1] == 3


async def test_query_validation() -> None:
    service, embeddings, vectors = build()
    with pytest.raises(InvalidRequestError) as excinfo:
        await service.search("   ")
    assert excinfo.value.code == "INVALID_QUERY"

    with pytest.raises(InvalidRequestError) as excinfo:
        await service.search("x" * 501)
    assert excinfo.value.code == "QUERY_TOO_LONG"

    for top_k in (0, 6):
        with pytest.raises(InvalidRequestError) as excinfo:
            await service.search("dog", top_k)
        assert excinfo.value.code == "INVALID_TOP_K"

    assert embeddings.text_calls == []
    assert vectors.search_calls == []


async def test_empty_collection_returns_empty_results() -> None:
    settings = make_settings()
    service = SearchService(settings, FakeEmbeddings(), FakeVectorStore())
    response = await service.search("dog")
    assert response.results == []
