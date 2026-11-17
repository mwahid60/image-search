"""In-memory ingestion queue with a single worker and job-level deduplication."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass

from app.config import Settings
from app.ingestion.pipeline import IngestionPipeline
from app.logging_config import get_logger
from app.models.ingestion import IngestionState, IngestionStatus

logger = get_logger(__name__)

_COMPLETED_MEMORY = 1000


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job_id: str
    enqueued: bool
    reason: str

    @property
    def accepted(self) -> bool:
        return self.enqueued


class IngestionQueue:
    """One worker for the MVP; enqueueing the same job twice is a no-op."""

    def __init__(self, settings: Settings, pipeline: IngestionPipeline) -> None:
        self._settings = settings
        self._pipeline = pipeline
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued: set[str] = set()
        self._running: set[str] = set()
        self._completed: OrderedDict[str, IngestionStatus] = OrderedDict()
        self._worker: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._processed = 0
        self._failures = 0

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._stop_event.clear()
        self._worker = asyncio.create_task(self._run_worker(), name="ingestion-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Ingestion worker stopped with an error")

    # ------------------------------------------------------------------ API
    async def enqueue(
        self, job_id: str, *, manifest_key: str | None = None, force: bool = False
    ) -> EnqueueResult:
        if job_id in self._running:
            return EnqueueResult(job_id, False, "already_running")
        if job_id in self._queued:
            return EnqueueResult(job_id, False, "already_queued")
        if not force and job_id in self._completed:
            return EnqueueResult(job_id, False, "already_completed")
        self._queued.add(job_id)
        self._queue.put_nowait(job_id)
        logger.info("Enqueued ingestion job %s (force=%s)", job_id, force)
        return EnqueueResult(job_id, True, "enqueued")

    def status_snapshot(self) -> dict[str, object]:
        return {
            "queued": self._queue.qsize(),
            "queuedJobs": sorted(self._queued),
            "runningJobs": sorted(self._running),
            "processed": self._processed,
            "failures": self._failures,
        }

    def is_active(self, job_id: str) -> bool:
        return job_id in self._queued or job_id in self._running

    async def join(self) -> None:
        """Wait until the queue is drained (used by tests and shutdown)."""
        await self._queue.join()

    # ------------------------------------------------------------------ worker
    async def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                job_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            self._queued.discard(job_id)
            self._running.add(job_id)
            try:
                status = await self._pipeline.ingest_job(job_id)
                self._remember(job_id, status)
                if status.status == "FAILED":
                    self._failures += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self._failures += 1
                logger.exception("Ingestion worker failed for job %s", job_id)
            finally:
                self._running.discard(job_id)
                self._processed += 1
                self._queue.task_done()

    def _remember(self, job_id: str, status: IngestionStatus) -> None:
        # Only successful jobs are deduplicated in memory; FAILED jobs must stay
        # retryable by reconciliation without needing a manual force.
        if status.status != IngestionState.INDEXED:
            return
        self._completed[job_id] = status
        self._completed.move_to_end(job_id)
        while len(self._completed) > _COMPLETED_MEMORY:
            self._completed.popitem(last=False)
