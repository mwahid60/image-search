"""API contract tests with fakes (TestClient, no external services)."""

from __future__ import annotations

import json

from app.container import AppContainer
from app.errors import EmbeddingError, UpstreamError
from app.integrations.java import JavaFetchJob
from app.main import create_app
from app.models.ingestion import IngestionError, IngestionState, IngestionStatus
from app.models.records import ImageVectorRecord
from app.utils import Clock
from fastapi.testclient import TestClient
from tests.support import (
    FakeEmbeddings,
    FakeJavaClient,
    FakeObjectStore,
    FakeVectorStore,
    MutableClock,
    java_status,
    make_settings,
    manifest_document,
    manifest_image,
    png_bytes,
    sha,
    upstream_rejected,
)
from tests.test_java_contract import JAVA_FETCH_JOB_PAYLOAD

JOB = "fetch-test-001"


def build(**overrides):
    settings = make_settings(**overrides)
    java = FakeJavaClient()
    objects = FakeObjectStore()
    embeddings = FakeEmbeddings(dimension=settings.embedding_dimension)
    vectors = FakeVectorStore()
    container = AppContainer(
        settings=settings,
        java_client=java,
        object_store=objects,
        embeddings=embeddings,
        vector_store=vectors,
        clock=Clock(MutableClock().now),
    )
    app = create_app(settings, container)
    return app, container, java, objects, embeddings, vectors


def store_job(settings, objects, *, job_id: str = JOB, ingestion_state: str | None = "INDEXED"):
    data = png_bytes("red")
    document = manifest_document(job_id=job_id, images=[manifest_image(data)])
    objects.objects[settings.manifest_key(job_id)] = json.dumps(document).encode()
    objects.objects[document["images"][0]["objectKey"]] = data
    if ingestion_state:
        status = IngestionStatus(
            job_id=job_id,
            status=IngestionState(ingestion_state),
            total_images=1,
            indexed=1 if ingestion_state in {"INDEXED", "PARTIAL"} else 0,
            duplicates=0,
            failed=1 if ingestion_state == "PARTIAL" else 0,
            errors=[],
            started_at="2026-11-16T11:00:00Z",
            completed_at="2026-11-16T11:01:00Z",
        )
        objects.objects[settings.ingestion_status_key(job_id)] = status.model_dump_json(
            by_alias=True
        ).encode()
    return data, document


def test_health() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_components_and_sse() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"]["milvus"]["ready"] is True
    assert body["components"]["minio"]["ready"] is True
    assert body["components"]["llamaCpp"]["ready"] is True
    assert "sseConsumer" in body["components"]
    assert body["ingestionQueue"]["queued"] == 0


def test_ready_returns_503_when_dependency_fails() -> None:
    app, container, *_ = build()
    container.vector_store.fail_ping = RuntimeError("milvus down")
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["components"]["milvus"]["ready"] is False


def test_fetch_job_proxy_forwards_camel_case_payload() -> None:
    app, _, java, *_ = build()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fetch-jobs",
            json={"classes": ["dog", "cat", "dog"], "imagesPerClass": 5},
        )
    assert response.status_code == 202
    assert response.json() == {"jobId": "fetch-java-001", "status": "FETCHING"}
    assert java.create_calls == [(["dog", "cat"], 5)]


def test_fetch_job_proxy_maps_java_rejection_to_400() -> None:
    app, _, java, *_ = build()
    java.create_error = upstream_rejected()
    with TestClient(app) as client:
        response = client.post("/api/v1/fetch-jobs", json={"classes": ["dog"], "imagesPerClass": 2})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "JAVA_REJECTED_REQUEST"


def test_fetch_job_proxy_maps_java_unavailable_to_502() -> None:
    app, _, java, *_ = build()
    java.create_error = UpstreamError("down", code="JAVA_UNAVAILABLE")
    with TestClient(app) as client:
        response = client.post("/api/v1/fetch-jobs", json={"classes": ["dog"], "imagesPerClass": 2})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "JAVA_UNAVAILABLE"


def test_fetch_job_request_validation_errors_are_structured() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        response = client.post("/api/v1/fetch-jobs", json={"classes": [], "imagesPerClass": 5})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["details"]


def test_fetch_job_limit_validation_uses_settings() -> None:
    app, *_ = build(max_classes_per_request=2, max_images_per_class=5)
    with TestClient(app) as client:
        too_many = client.post(
            "/api/v1/fetch-jobs",
            json={"classes": ["dog", "cat", "bird"], "imagesPerClass": 2},
        )
        too_many_images = client.post(
            "/api/v1/fetch-jobs", json={"classes": ["dog"], "imagesPerClass": 6}
        )
    assert too_many.status_code == 400
    assert too_many.json()["error"]["code"] == "TOO_MANY_CLASSES"
    assert too_many_images.status_code == 400
    assert too_many_images.json()["error"]["code"] == "INVALID_IMAGES_PER_CLASS"


def test_aggregated_status_combines_java_manifest_and_ingestion() -> None:
    app, container, java, objects, *_ = build()
    store_job(container.settings, objects)
    java.status_job = java_status(JOB)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/fetch-jobs/{JOB}")
    assert response.status_code == 200
    body = response.json()
    assert body["jobId"] == JOB
    assert body["fetchStatus"] == "COMPLETED"
    assert body["ingestionStatus"] == "INDEXED"
    assert body["downloaded"] == 1
    assert body["failedDownloads"] == 0
    assert body["indexed"] == 1
    assert body["duplicates"] == 0
    assert body["errors"] == []
    assert body["warnings"] == []
    assert body["manifestKey"] == container.settings.manifest_key(JOB)


def test_aggregated_status_tolerates_java_outage() -> None:
    app, container, java, objects, *_ = build()
    store_job(container.settings, objects, ingestion_state=None)
    java.status_error = UpstreamError("down", code="JAVA_UNAVAILABLE")
    with TestClient(app) as client:
        response = client.get(f"/api/v1/fetch-jobs/{JOB}")
    assert response.status_code == 200
    body = response.json()
    assert body["fetchStatus"] == "COMPLETED"
    assert body["ingestionStatus"] == "PENDING"
    assert any("java status unavailable" in warning for warning in body["warnings"])


def test_aggregated_status_includes_ingestion_errors() -> None:
    app, container, java, objects, *_ = build()
    store_job(container.settings, objects)
    status = IngestionStatus(
        job_id=JOB,
        status=IngestionState.PARTIAL,
        total_images=2,
        indexed=1,
        duplicates=0,
        failed=1,
        errors=[IngestionError(image_id="a" * 64, stage="embedding", reason="boom")],
    )
    objects.objects[container.settings.ingestion_status_key(JOB)] = status.model_dump_json(
        by_alias=True
    ).encode()
    java.status_job = java_status(JOB)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/fetch-jobs/{JOB}")
    body = response.json()
    assert body["ingestionStatus"] == "PARTIAL"
    assert body["failed"] == 1
    assert body["errors"][0]["stage"] == "embedding"


def test_unknown_job_returns_404() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        response = client.get("/api/v1/fetch-jobs/fetch-unknown")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_manual_ingest_enqueues_with_force() -> None:
    app, container, _, objects, *_ = build()
    store_job(container.settings, objects, ingestion_state=None)
    with TestClient(app) as client:
        first = client.post(f"/api/v1/ingest/{JOB}")
        second = client.post(f"/api/v1/ingest/{JOB}")
    assert first.status_code == 202
    assert first.json()["status"] == "ENQUEUED"
    assert second.status_code == 202
    assert second.json()["status"] == "ALREADY_RUNNING"
    assert container.queue.is_active(JOB)


def test_manual_ingest_bypasses_automatic_attempt_limit() -> None:
    app, container, _, objects, *_ = build()
    store_job(container.settings, objects, ingestion_state="PARTIAL")
    status = IngestionStatus(
        job_id=JOB,
        status=IngestionState.PARTIAL,
        total_images=2,
        indexed=1,
        failed=1,
        attempts=99,
        errors=[IngestionError(stage="embedding", reason="boom")],
    )
    objects.objects[container.settings.ingestion_status_key(JOB)] = status.model_dump_json(
        by_alias=True
    ).encode()
    with TestClient(app) as client:
        response = client.post(f"/api/v1/ingest/{JOB}")
    assert response.status_code == 202
    assert response.json()["status"] == "ENQUEUED"
    assert container.queue.is_active(JOB)


def test_manual_ingest_requires_manifest() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        response = client.post("/api/v1/ingest/fetch-missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MANIFEST_NOT_FOUND"


def test_manual_ingest_validates_job_id() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        response = client.post("/api/v1/ingest/bad%20job")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_JOB_ID"


def test_search_returns_whitelisted_metadata_only() -> None:
    app, _, _, objects, embeddings, vectors = build()
    data = png_bytes("green")
    digest = sha(data)
    objects.objects[f"images/bank-mas/{digest}.png"] = data
    vectors.records[digest] = ImageVectorRecord(
        image_id=digest,
        embedding=[0.1] * 768,
        object_key=f"images/bank-mas/{digest}.png",
        content_type="image/png",
        width=16,
        height=12,
        sha256=digest,
        source_url="https://example.test",
        license="cc-by",
        creator="Creator",
        evaluation_metadata={"title": "SECRET", "tags": ["secret"], "requestedClass": "dog"},
        created_at=1,
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "a dog in the snow", "topK": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "a dog in the snow"
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert set(result) == {
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
    assert "SECRET" not in json.dumps(body)
    assert embeddings.text_calls == ["a dog in the snow"]


def test_search_validation() -> None:
    app, *_ = build(search_max_top_k=20)
    with TestClient(app) as client:
        empty = client.post("/api/v1/search", json={"query": "   "})
        too_large_schema = client.post("/api/v1/search", json={"query": "dog", "topK": 51})
        too_large_settings = client.post("/api/v1/search", json={"query": "dog", "topK": 21})
    assert empty.status_code == 422
    assert too_large_schema.status_code == 422
    assert too_large_settings.status_code == 400
    assert too_large_settings.json()["error"]["code"] == "INVALID_TOP_K"


def test_image_endpoint_streams_bytes_from_object_store() -> None:
    app, _, _, objects, _, vectors = build()
    data = png_bytes("blue")
    digest = sha(data)
    object_key = f"images/bank-mas/{digest}.png"
    objects.objects[object_key] = data
    vectors.records[digest] = ImageVectorRecord(
        image_id=digest,
        embedding=[0.1] * 768,
        object_key=object_key,
        content_type="image/png",
        width=16,
        height=12,
        sha256=digest,
        source_url="https://example.test",
        license="cc-by",
        creator="Creator",
        created_at=1,
    )
    with TestClient(app) as client:
        response = client.get(f"/api/v1/images/{digest}")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"
    assert response.headers["etag"] == f'"{digest}"'
    assert response.headers["cache-control"] == "public, max-age=86400"


def test_image_endpoint_unknown_or_invalid_id() -> None:
    app, *_ = build()
    with TestClient(app) as client:
        unknown = client.get("/api/v1/images/" + "b" * 64)
        invalid = client.get("/api/v1/images/not-a-hash")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "IMAGE_NOT_FOUND"
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_IMAGE_ID"


def test_search_maps_embedding_failure_to_503() -> None:
    app, _, _, _, embeddings, _ = build()
    embeddings.fail_text = EmbeddingError(
        "embedding service down", code="EMBEDDING_UNAVAILABLE", status_code=503
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "dog"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMBEDDING_UNAVAILABLE"


def test_image_endpoint_reports_missing_object() -> None:
    app, _, _, objects, _, vectors = build()
    digest = "c" * 64
    vectors.records[digest] = ImageVectorRecord(
        image_id=digest,
        embedding=[0.1] * 768,
        object_key=f"images/bank-mas/{digest}.png",
        content_type="image/png",
        width=16,
        height=12,
        sha256=digest,
        source_url="https://example.test",
        license="cc-by",
        creator="Creator",
        created_at=1,
    )
    assert objects.objects == {}
    with TestClient(app) as client:
        response = client.get(f"/api/v1/images/{digest}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMAGE_OBJECT_NOT_FOUND"


def test_aggregated_status_reports_fetching_before_manifest_exists() -> None:
    app, _, java, *_ = build()
    java.status_job = JavaFetchJob.model_validate(JAVA_FETCH_JOB_PAYLOAD)
    with TestClient(app) as client:
        response = client.get("/api/v1/fetch-jobs/fetch-20261116-001")
    assert response.status_code == 200
    body = response.json()
    assert body["fetchStatus"] == "FETCHING"
    assert body["ingestionStatus"] == "UNKNOWN"
    assert body["downloaded"] == 0
    assert body["failedDownloads"] == 0
    assert body["duplicates"] == 0
    assert body["manifestKey"] is None
    assert body["requestedClasses"] == ["dog"]
    assert body["startedAt"] == "2026-11-16T11:00:00Z"
    assert body["completedAt"] is None
