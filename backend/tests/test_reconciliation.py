"""Reconciliation tests: startup/periodic scans and status combinations."""

from __future__ import annotations

import json

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.reconciliation import ReconciliationService
from app.models.ingestion import IngestionError, IngestionState, IngestionStatus
from tests.support import (
    FakeEmbeddings,
    FakeObjectStore,
    FakeQueue,
    FakeVectorStore,
    make_settings,
    manifest_document,
    manifest_image,
    png_bytes,
)

JOB_A = "fetch-a"
JOB_B = "fetch-b"
JOB_C = "fetch-c"


def add_manifest(objects: FakeObjectStore, settings, job_id: str, images=None, **kwargs) -> None:
    if images is None:
        images = [manifest_image(png_bytes("red"))]
    document = manifest_document(job_id=job_id, images=images, **kwargs)
    objects.objects[settings.manifest_key(job_id)] = json.dumps(document).encode()


def add_status(objects: FakeObjectStore, settings, job_id: str, state: str) -> None:
    status = IngestionStatus(
        job_id=job_id,
        status=IngestionState(state),
        total_images=1,
        indexed=1 if state == "INDEXED" else 0,
    )
    objects.objects[settings.ingestion_status_key(job_id)] = status.model_dump_json(
        by_alias=True
    ).encode()


def build(objects: FakeObjectStore, pipeline=None, **overrides):
    settings = make_settings(**overrides)
    queue = FakeQueue()
    actual_pipeline = pipeline or IngestionPipeline(
        settings, objects, FakeEmbeddings(), FakeVectorStore()
    )
    service = ReconciliationService(settings, objects, queue, actual_pipeline)
    return service, queue


async def test_missing_status_is_enqueued() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    service, queue = build(objects)
    report = await service.run_once("startup")
    assert report.manifests == 1
    assert report.enqueued == 1
    assert queue.enqueued == [(JOB_A, False)]
    assert service.last_report["trigger"] == "startup"


async def test_indexed_status_is_skipped() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    add_status(objects, settings, JOB_A, "INDEXED")
    service, queue = build(objects)
    report = await service.run_once()
    assert report.skipped == 1
    assert queue.enqueued == []


async def test_pending_and_ingesting_are_retried() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    add_manifest(objects, settings, JOB_B)
    add_status(objects, settings, JOB_A, "PENDING")
    add_status(objects, settings, JOB_B, "INGESTING")
    service, queue = build(objects)
    report = await service.run_once()
    assert report.enqueued == 2
    assert {job for job, _ in queue.enqueued} == {JOB_A, JOB_B}


async def test_failed_status_respects_retry_configuration() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    add_status(objects, settings, JOB_A, "FAILED")
    service, queue = build(objects)
    await service.run_once()
    assert queue.enqueued == []

    service, queue = build(objects, reconciliation_retry_failed=True)
    await service.run_once()
    assert queue.enqueued == [(JOB_A, False)]

    service, queue = build(objects)
    await service.run_once(retry_failed=True)
    assert queue.enqueued == [(JOB_A, False)]


async def test_failed_or_empty_manifests_are_skipped() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A, status="FAILED")
    add_manifest(objects, settings, JOB_B, images=[])
    service, queue = build(objects)
    report = await service.run_once()
    assert report.enqueued == 0
    assert queue.enqueued == []


async def test_active_job_is_not_enqueued_twice() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    queue = FakeQueue()
    queue.is_active = lambda job_id: job_id == JOB_A  # type: ignore[method-assign]
    service = ReconciliationService(
        settings,
        objects,
        queue,
        IngestionPipeline(settings, objects, FakeEmbeddings(), FakeVectorStore()),
    )
    report = await service.run_once()
    assert report.enqueued == 0
    assert report.skipped == 1


async def test_malformed_manifest_does_not_stop_scan() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    objects.objects[settings.manifest_key(JOB_B)] = b"{not json"
    add_manifest(objects, settings, JOB_C)
    service, queue = build(objects)
    report = await service.run_once()
    assert report.manifests == 3
    assert report.errors == 1
    assert report.enqueued == 2
    assert {job for job, _ in queue.enqueued} == {JOB_A, JOB_C}


async def test_manifest_job_id_mismatch_is_ignored() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    document = manifest_document(
        job_id="fetch-someone-else", images=[manifest_image(png_bytes("blue"))]
    )
    objects.objects[settings.manifest_key(JOB_A)] = json.dumps(document).encode()
    service, queue = build(objects)
    report = await service.run_once()
    assert report.enqueued == 0
    assert queue.enqueued == []


async def test_invalid_job_ids_and_legacy_prefixes_are_ignored() -> None:
    objects = FakeObjectStore()
    objects.objects["jobs/bank-mas/..%2Ffetch/fetch-manifest.json"] = b"{}"
    objects.objects["legacy/jobs/bank-mas/fetch-x/fetch-manifest.json"] = b"{}"
    objects.objects["jobs/bank-mas/fetch-with/slash/fetch-manifest.json"] = b"{}"
    service, queue = build(objects)
    report = await service.run_once()
    assert report.manifests == 0
    assert queue.enqueued == []


async def test_cached_status_used_before_persisted_status() -> None:
    settings = make_settings()
    objects = FakeObjectStore()
    add_manifest(objects, settings, JOB_A)
    add_status(objects, settings, JOB_A, "PENDING")  # stale persisted snapshot
    pipeline = IngestionPipeline(settings, objects, FakeEmbeddings(), FakeVectorStore())
    pipeline._status_cache[JOB_A] = IngestionStatus(
        job_id=JOB_A,
        status=IngestionState.FAILED,
        errors=[IngestionError(stage="embedding", reason="boom")],
    )
    service, queue = build(objects, pipeline=pipeline)
    report = await service.run_once()
    assert report.enqueued == 0
    assert queue.enqueued == []


async def test_list_failure_is_reported_without_raising() -> None:
    class BrokenStore(FakeObjectStore):
        async def list_keys(self, prefix: str) -> list[str]:
            raise RuntimeError("minio down")

    service, queue = build(BrokenStore())
    report = await service.run_once()
    assert report.errors == 1
    assert queue.enqueued == []
