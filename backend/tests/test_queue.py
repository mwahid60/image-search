"""Ingestion queue tests: single worker, job dedup, failure isolation, SSE wiring."""

from __future__ import annotations

import asyncio
import json

import pytest
from app.container import AppContainer
from app.errors import EmbeddingError
from app.ingestion.queue import IngestionQueue
from app.integrations.sse import FetchCompletedEvent
from app.models.ingestion import IngestionState, IngestionStatus
from app.utils import Clock
from tests.support import (
    FakeEmbeddings,
    FakeJavaClient,
    FakeObjectStore,
    FakeVectorStore,
    MutableClock,
    make_settings,
    manifest_document,
    manifest_image,
    png_bytes,
)


class RecordingPipeline:
    def __init__(self, delay: float = 0.0, fail_jobs: set[str] | None = None) -> None:
        self.jobs: list[str] = []
        self.started = asyncio.Event()
        self.delay = delay
        self.fail_jobs = fail_jobs or set()

    async def ingest_job(self, job_id: str) -> IngestionStatus:
        self.jobs.append(job_id)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if job_id in self.fail_jobs:
            raise EmbeddingError("boom", code="EMBEDDING_UNAVAILABLE")
        return IngestionStatus(
            job_id=job_id,
            status=IngestionState.INDEXED,
            total_images=1,
            indexed=1,
        )


async def _drain(queue: IngestionQueue) -> None:
    await asyncio.wait_for(queue.join(), timeout=2)


async def test_duplicate_enqueue_before_worker_starts_runs_once() -> None:
    pipeline = RecordingPipeline()
    queue = IngestionQueue(make_settings(), pipeline)  # type: ignore[arg-type]
    first = await queue.enqueue("fetch-1")
    second = await queue.enqueue("fetch-1")
    assert first.enqueued is True
    assert second.enqueued is False
    assert second.reason == "already_queued"

    await queue.start()
    try:
        await _drain(queue)
    finally:
        await queue.stop()
    assert pipeline.jobs == ["fetch-1"]


async def test_duplicate_enqueue_while_running_is_rejected() -> None:
    pipeline = RecordingPipeline(delay=0.2)
    queue = IngestionQueue(make_settings(), pipeline)  # type: ignore[arg-type]
    await queue.start()
    try:
        await queue.enqueue("fetch-1")
        await asyncio.wait_for(pipeline.started.wait(), timeout=2)
        duplicate = await queue.enqueue("fetch-1")
        assert duplicate.enqueued is False
        assert duplicate.reason == "already_running"
        assert queue.is_active("fetch-1") is True
        await _drain(queue)
    finally:
        await queue.stop()
    assert pipeline.jobs == ["fetch-1"]


async def test_completed_jobs_require_force_to_retry() -> None:
    pipeline = RecordingPipeline()
    queue = IngestionQueue(make_settings(), pipeline)  # type: ignore[arg-type]
    await queue.start()
    try:
        await queue.enqueue("fetch-1")
        await _drain(queue)
        skipped = await queue.enqueue("fetch-1")
        assert skipped.enqueued is False
        assert skipped.reason == "already_completed"

        forced = await queue.enqueue("fetch-1", force=True)
        assert forced.enqueued is True
        await _drain(queue)
    finally:
        await queue.stop()
    assert pipeline.jobs == ["fetch-1", "fetch-1"]


async def test_worker_survives_pipeline_exceptions() -> None:
    pipeline = RecordingPipeline(fail_jobs={"fetch-bad"})
    queue = IngestionQueue(make_settings(), pipeline)  # type: ignore[arg-type]
    await queue.start()
    try:
        await queue.enqueue("fetch-bad")
        await queue.enqueue("fetch-good")
        await _drain(queue)
    finally:
        await queue.stop()
    assert pipeline.jobs == ["fetch-bad", "fetch-good"]
    snapshot = queue.status_snapshot()
    assert snapshot["processed"] == 2
    assert snapshot["failures"] == 1


async def test_sse_handler_enqueues_and_worker_indexes() -> None:
    settings = make_settings()
    data = png_bytes("red")
    document = manifest_document(job_id="fetch-sse-001", images=[manifest_image(data)])
    objects = FakeObjectStore(
        {
            settings.manifest_key("fetch-sse-001"): json.dumps(document).encode(),
            document["images"][0]["objectKey"]: data,
        }
    )
    vectors = FakeVectorStore()
    container = AppContainer(
        settings=settings,
        java_client=FakeJavaClient(),
        object_store=objects,
        embeddings=FakeEmbeddings(),
        vector_store=vectors,
        clock=Clock(MutableClock().now),
    )
    await container.queue.start()
    try:
        await container._handle_fetch_completed(
            FetchCompletedEvent(
                event_id="fetch:fetch-sse-001:completed",
                job_id="fetch-sse-001",
                manifest_key=settings.manifest_key("fetch-sse-001"),
            )
        )
        await asyncio.wait_for(container.queue.join(), timeout=3)
    finally:
        await container.queue.stop()

    assert len(vectors.records) == 1
    persisted = IngestionStatus.model_validate_json(
        objects.objects[settings.ingestion_status_key("fetch-sse-001")]
    )
    assert persisted.status == IngestionState.INDEXED
    assert persisted.indexed == 1


async def test_join_returns_after_empty_queue() -> None:
    queue = IngestionQueue(make_settings(), RecordingPipeline())  # type: ignore[arg-type]
    await queue.start()
    try:
        await asyncio.wait_for(queue.join(), timeout=1)
    finally:
        await queue.stop()


def test_ingestion_status_requires_valid_state() -> None:
    with pytest.raises(ValueError):
        IngestionStatus(job_id="fetch-1", status="BROKEN")


async def test_two_sse_events_for_same_job_enqueue_only_once() -> None:
    settings = make_settings()
    data = png_bytes("violet")
    document = manifest_document(job_id="fetch-sse-002", images=[manifest_image(data)])
    objects = FakeObjectStore(
        {
            settings.manifest_key("fetch-sse-002"): json.dumps(document).encode(),
            document["images"][0]["objectKey"]: data,
        }
    )
    vectors = FakeVectorStore()
    container = AppContainer(
        settings=settings,
        java_client=FakeJavaClient(),
        object_store=objects,
        embeddings=FakeEmbeddings(),
        vector_store=vectors,
        clock=Clock(MutableClock().now),
    )
    event = FetchCompletedEvent(
        event_id="evt-a",
        job_id="fetch-sse-002",
        manifest_key=settings.manifest_key("fetch-sse-002"),
    )
    await container._handle_fetch_completed(event)
    await container._handle_fetch_completed(
        FetchCompletedEvent(
            event_id="evt-b",
            job_id="fetch-sse-002",
            manifest_key=settings.manifest_key("fetch-sse-002"),
        )
    )
    await container.queue.start()
    try:
        await asyncio.wait_for(container.queue.join(), timeout=3)
    finally:
        await container.queue.stop()
    assert container.queue.status_snapshot()["processed"] == 1
    assert len(vectors.records) == 1


class FailedStatusPipeline(RecordingPipeline):
    async def ingest_job(self, job_id: str) -> IngestionStatus:
        self.jobs.append(job_id)
        return IngestionStatus(
            job_id=job_id,
            status=IngestionState.FAILED,
            total_images=1,
            indexed=0,
            failed=1,
            errors=[],
        )


async def test_failed_job_stays_retryable_without_force() -> None:
    pipeline = FailedStatusPipeline()
    queue = IngestionQueue(make_settings(), pipeline)  # type: ignore[arg-type]
    await queue.start()
    try:
        await queue.enqueue("fetch-failed")
        await _drain(queue)
        retry = await queue.enqueue("fetch-failed")
        assert retry.enqueued is True
        await _drain(queue)
    finally:
        await queue.stop()
    assert pipeline.jobs == ["fetch-failed", "fetch-failed"]
