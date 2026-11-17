"""Aggregated fetch status safety across missing manifest/ingestion combinations."""

from __future__ import annotations

import json

from app.ingestion.pipeline import IngestionPipeline
from app.models.ingestion import IngestionState, IngestionStatus
from app.services.fetch_status import FetchStatusService
from tests.support import (
    FakeEmbeddings,
    FakeJavaClient,
    FakeObjectStore,
    FakeVectorStore,
    make_settings,
    manifest_document,
)
from tests.test_java_contract import JAVA_FETCH_JOB_PAYLOAD

JOB = "fetch-status-001"


def build(objects: FakeObjectStore, java_payload: dict | None = None):
    settings = make_settings()
    java = FakeJavaClient()
    if java_payload is not None:
        from app.integrations.java import JavaFetchJob

        java.status_job = JavaFetchJob.model_validate(java_payload)
    pipeline = IngestionPipeline(settings, objects, FakeEmbeddings(), FakeVectorStore())
    return FetchStatusService(settings, java, objects, pipeline), settings


def store_manifest(objects: FakeObjectStore, settings, completed_at: str | None) -> None:
    document = manifest_document(job_id=JOB, images=[])
    document["completedAt"] = completed_at
    objects.objects[settings.manifest_key(JOB)] = json.dumps(document).encode()


def store_ingestion_status(objects: FakeObjectStore, settings, completed_at: str | None) -> None:
    status = IngestionStatus(
        job_id=JOB,
        status=IngestionState.INDEXED,
        total_images=0,
        indexed=0,
        completed_at=completed_at,
    )
    objects.objects[settings.ingestion_status_key(JOB)] = status.model_dump_json(
        by_alias=True
    ).encode()


async def test_manifest_without_ingestion_status_does_not_crash() -> None:
    objects = FakeObjectStore()
    service, settings = build(objects)
    store_manifest(objects, settings, "2026-11-16T11:05:00Z")

    status = await service.get_status(JOB)

    assert status.fetch_status == "COMPLETED"
    assert status.ingestion_status == "PENDING"
    assert status.completed_at == "2026-11-16T11:05:00Z"


async def test_manifest_without_completed_at_and_without_ingestion_is_none() -> None:
    objects = FakeObjectStore()
    service, settings = build(objects)
    store_manifest(objects, settings, None)

    status = await service.get_status(JOB)

    assert status.completed_at is None
    assert status.ingestion_status == "PENDING"


async def test_ingestion_status_without_manifest_uses_ingestion_completed_at() -> None:
    objects = FakeObjectStore()
    service, settings = build(objects)
    store_ingestion_status(objects, settings, "2026-11-16T11:07:00Z")

    status = await service.get_status(JOB)

    assert status.fetch_status == "UNKNOWN"
    assert status.ingestion_status == "INDEXED"
    assert status.completed_at == "2026-11-16T11:07:00Z"


async def test_manifest_completed_at_takes_precedence_over_ingestion() -> None:
    objects = FakeObjectStore()
    service, settings = build(objects)
    store_manifest(objects, settings, "2026-11-16T11:05:00Z")
    store_ingestion_status(objects, settings, "2026-11-16T11:07:00Z")

    status = await service.get_status(JOB)

    assert status.completed_at == "2026-11-16T11:05:00Z"


async def test_java_only_payload_before_manifest() -> None:
    objects = FakeObjectStore()
    service, _ = build(objects, JAVA_FETCH_JOB_PAYLOAD)

    status = await service.get_status(JOB)

    assert status.fetch_status == "FETCHING"
    assert status.ingestion_status == "UNKNOWN"
    assert status.downloaded == 0
    assert status.failed_downloads == 0
    assert status.duplicates == 0
    assert status.manifest_key is None
    assert status.requested_classes == ["dog"]
    assert status.started_at == "2026-11-16T11:00:00Z"
    assert status.completed_at is None
    assert status.warnings == []
