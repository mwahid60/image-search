"""Ingestion pipeline tests: determinism, idempotency, failure isolation, status."""

from __future__ import annotations

import json
import math

from app.ingestion.pipeline import IngestionPipeline
from app.models.ingestion import IngestionState
from app.utils import Clock
from tests.support import (
    FakeEmbeddings,
    FakeObjectStore,
    FakeVectorStore,
    MutableClock,
    make_settings,
    manifest_document,
    manifest_image,
    png_bytes,
    sha,
)


def build_pipeline(images, *, status="COMPLETED", failed_downloads=None, settings=None, clock=None):
    settings = settings or make_settings()
    objects = FakeObjectStore()
    clean_images = []
    for image in images:
        clean = {key: value for key, value in image.items() if key != "_bytes"}
        if image.get("_bytes") is not None:
            objects.objects[image["objectKey"]] = image["_bytes"]
        clean_images.append(clean)
    document = manifest_document(
        images=clean_images, status=status, failed_downloads=failed_downloads
    )
    objects.objects[settings.manifest_key(document["jobId"])] = json.dumps(document).encode()
    embeddings = FakeEmbeddings(dimension=settings.embedding_dimension)
    vectors = FakeVectorStore()
    pipeline = IngestionPipeline(settings, objects, embeddings, vectors, clock=clock or Clock())
    return pipeline, objects, embeddings, vectors


def image_entry(data: bytes, **overrides):
    entry = manifest_image(data, **overrides)
    entry["_bytes"] = data
    return entry


async def test_ingestion_indexes_images_with_metadata_and_status() -> None:
    data = png_bytes("red")
    clock = MutableClock()
    pipeline, objects, embeddings, vectors = build_pipeline(
        [image_entry(data)], clock=Clock(clock.now)
    )
    status = await pipeline.ingest_job("fetch-test-001")

    assert status.status == IngestionState.INDEXED
    assert status.indexed == 1
    assert status.duplicates == 0
    assert status.failed == 0
    assert status.total_images == 1
    assert status.started_at and status.completed_at

    digest = sha(data)
    record = vectors.records[digest]
    assert record.object_key == f"images/bank-mas/{digest}.png"
    assert record.sha256 == digest
    assert record.content_type == "image/png"
    assert record.width == 16
    assert record.height == 12
    assert record.source_url.startswith("https://commons.wikimedia.org/")
    assert record.license == "cc-by"
    assert record.creator == "Test Creator"
    assert record.evaluation_metadata["title"] == "A dog in the snow"
    assert record.evaluation_metadata["requestedClass"] == "dog"
    norm = math.sqrt(sum(value * value for value in record.embedding))
    assert math.isclose(norm, 1.0, rel_tol=1e-9)
    assert embeddings.image_calls == [(data, "image/png")]

    persisted = json.loads(objects.objects[make_settings().ingestion_status_key("fetch-test-001")])
    assert persisted["jobId"] == "fetch-test-001"
    assert persisted["status"] == "INDEXED"
    assert persisted["indexed"] == 1
    assert persisted["totalImages"] == 1
    assert "startedAt" in persisted and "completedAt" in persisted


async def test_reingestion_is_idempotent_and_preserves_created_at() -> None:
    data = png_bytes("green")
    clock = MutableClock()
    pipeline, _objects, embeddings, vectors = build_pipeline(
        [image_entry(data)], clock=Clock(clock.now)
    )
    first = await pipeline.ingest_job("fetch-test-001")
    created_at = vectors.records[sha(data)].created_at

    clock.advance(3600)
    second = await pipeline.ingest_job("fetch-test-001")

    assert first.indexed == 1
    assert second.status == IngestionState.INDEXED
    assert second.indexed == 1
    assert second.duplicates == 1
    assert len(vectors.records) == 1
    assert vectors.records[sha(data)].created_at == created_at
    assert len(embeddings.image_calls) == 1


async def test_duplicate_images_inside_one_manifest_are_counted_once() -> None:
    data = png_bytes("blue")
    pipeline, _, embeddings, vectors = build_pipeline([image_entry(data), image_entry(data)])
    status = await pipeline.ingest_job("fetch-test-001")
    assert status.status == IngestionState.INDEXED
    assert status.indexed == 2
    assert status.duplicates == 1
    assert len(vectors.records) == 1
    assert len(embeddings.image_calls) == 1


async def test_one_bad_image_does_not_fail_the_job() -> None:
    good = png_bytes("yellow")
    bad = png_bytes("purple")
    bad_entry = image_entry(bad, sha256="a" * 64, image_id="a" * 64)
    pipeline, _, _, vectors = build_pipeline([image_entry(good), bad_entry])
    status = await pipeline.ingest_job("fetch-test-001")

    assert status.status == IngestionState.INDEXED
    assert status.indexed == 1
    assert status.failed == 1
    assert status.errors[0].stage == "validate"
    assert "sha256" in status.errors[0].reason
    assert sha(good) in vectors.records
    assert "a" * 64 not in vectors.records


async def test_legacy_object_prefix_is_rejected() -> None:
    data = png_bytes("orange")
    entry = image_entry(data, object_key=f"images/bank-mas-old/{sha(data)}.png")
    pipeline, _, embeddings, vectors = build_pipeline([entry])
    status = await pipeline.ingest_job("fetch-test-001")

    assert status.status == IngestionState.FAILED
    assert "prefix" in status.errors[0].reason
    assert embeddings.image_calls == []
    assert vectors.records == {}


async def test_undecodable_object_is_rejected() -> None:
    data = b"not-an-image"
    pipeline, _, embeddings, _vectors = build_pipeline([image_entry(data)])
    status = await pipeline.ingest_job("fetch-test-001")
    assert status.status == IngestionState.FAILED
    assert "decoded" in status.errors[0].reason
    assert embeddings.image_calls == []


async def test_failed_manifest_is_not_ingested() -> None:
    data = png_bytes("black")
    pipeline, _, embeddings, _ = build_pipeline([image_entry(data)], status="FAILED")
    status = await pipeline.ingest_job("fetch-test-001")
    assert status.status == IngestionState.FAILED
    assert status.errors[0].stage == "manifest"
    assert embeddings.image_calls == []


async def test_manifest_key_outside_contract_is_rejected() -> None:
    data = png_bytes("white")
    pipeline, objects, embeddings, _ = build_pipeline([image_entry(data)])
    # Put a valid manifest at a legacy location; the pipeline must refuse it.
    legacy_key = "jobs/bank-mas-legacy/fetch-test-001/fetch-manifest.json"
    objects.objects[legacy_key] = objects.objects[make_settings().manifest_key("fetch-test-001")]
    status = await pipeline.ingest_job("fetch-test-001", manifest_key=legacy_key)
    assert status.status == IngestionState.FAILED
    assert status.errors[0].stage == "INVALID_MANIFEST_KEY"
    assert embeddings.image_calls == []


async def test_manifest_job_id_mismatch_is_rejected() -> None:
    data = png_bytes("cyan")
    pipeline, objects, _, _ = build_pipeline([image_entry(data)])
    key = make_settings().manifest_key("fetch-test-001")
    document = json.loads(objects.objects[key])
    document["jobId"] = "fetch-other"
    objects.objects[key] = json.dumps(document).encode()
    status = await pipeline.ingest_job("fetch-test-001")
    assert status.status == IngestionState.FAILED
    assert "jobId" in status.errors[0].reason


async def test_upsert_failure_marks_job_failed() -> None:
    data = png_bytes("magenta")
    pipeline, _, _, vectors = build_pipeline([image_entry(data)])
    vectors.fail_upsert = RuntimeError("milvus down")
    status = await pipeline.ingest_job("fetch-test-001")
    assert status.status == IngestionState.FAILED
    assert status.indexed == 0
    assert status.errors[0].stage == "upsert"


async def test_missing_manifest_records_failed_status() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    pipeline = IngestionPipeline(
        settings, objects, FakeEmbeddings(), FakeVectorStore(), clock=Clock()
    )
    status = await pipeline.ingest_job("fetch-missing")
    assert status.status == IngestionState.FAILED
    assert status.errors[0].stage == "MANIFEST_NOT_FOUND"
    assert settings.ingestion_status_key("fetch-missing") in objects.objects


async def test_persisted_status_can_be_read_back() -> None:
    data = png_bytes("brown")
    pipeline, _, _, _ = build_pipeline([image_entry(data)])
    await pipeline.ingest_job("fetch-test-001")
    persisted = await pipeline.read_persisted_status("fetch-test-001")
    assert persisted is not None
    assert persisted.status == IngestionState.INDEXED
    assert persisted.indexed == 1


async def test_configured_pixel_limit_fails_ingestion() -> None:
    data = png_bytes("teal", size=(16, 12))  # 192 pixels
    pipeline, _, embeddings, vectors = build_pipeline(
        [image_entry(data)], settings=make_settings(ingest_max_image_pixels=100)
    )
    status = await pipeline.ingest_job("fetch-test-001")

    assert status.status == IngestionState.FAILED
    assert status.errors[0].stage == "validate"
    assert "pixel count 192" in status.errors[0].reason
    assert embeddings.image_calls == []
    assert vectors.records == {}
