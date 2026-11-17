"""Reconciliation of MinIO fetch manifests against ingestion status.

SSE stays the primary trigger; reconciliation covers missed events and crashes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.config import Settings
from app.errors import AppError
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.queue import IngestionQueue
from app.integrations.object_store import ObjectStore
from app.logging_config import get_logger
from app.models.ingestion import IngestionState, IngestionStatus
from app.models.manifest import FetchManifest
from app.utils import validate_job_id

logger = get_logger(__name__)


@dataclass(slots=True)
class ReconciliationReport:
    trigger: str
    manifests: int = 0
    enqueued: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "trigger": self.trigger,
            "manifests": self.manifests,
            "enqueued": self.enqueued,
            "skipped": self.skipped,
            "errors": self.errors,
            "details": self.details[:20],
        }


class ReconciliationService:
    def __init__(
        self,
        settings: Settings,
        object_store: ObjectStore,
        queue: IngestionQueue,
        pipeline: IngestionPipeline,
    ) -> None:
        self._settings = settings
        self._objects = object_store
        self._queue = queue
        self._pipeline = pipeline
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_report: ReconciliationReport | None = None
        self._runs = 0

    @property
    def last_report(self) -> dict[str, object] | None:
        return self._last_report.as_dict() if self._last_report else None

    async def start(self) -> None:
        if not self._settings.reconciliation_enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="reconciliation")

    async def stop(self) -> None:
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Reconciliation stopped with an error")

    async def _run_loop(self) -> None:
        if self._settings.reconciliation_on_startup:
            await self.run_once(trigger="startup")
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.reconciliation_interval_seconds,
                )
                break
            except TimeoutError:
                pass
            await self.run_once(trigger="periodic")

    async def run_once(
        self, trigger: str = "manual", *, retry_failed: bool | None = None
    ) -> ReconciliationReport:
        report = ReconciliationReport(trigger=trigger)
        retry_failed = (
            self._settings.reconciliation_retry_failed if retry_failed is None else retry_failed
        )
        self._runs += 1
        try:
            keys = await self._objects.list_keys(self._settings.manifest_scan_prefix())
        except AppError as exc:
            report.errors += 1
            report.details.append(f"list failed: {exc.message}")
            logger.warning("Reconciliation could not list manifests: %s", exc.message)
            self._last_report = report
            return report
        except Exception as exc:
            report.errors += 1
            report.details.append(f"list failed: {type(exc).__name__}")
            logger.exception("Reconciliation failed to list manifests")
            self._last_report = report
            return report

        for key in sorted(keys):
            job_id = self._job_id_from_key(key)
            if job_id is None:
                continue
            report.manifests += 1
            try:
                outcome = await self._maybe_enqueue(job_id, key, retry_failed=retry_failed)
            except AppError as exc:
                report.errors += 1
                report.details.append(f"{job_id}: {exc.message}")
                logger.warning("Reconciliation error for job %s: %s", job_id, exc.message)
                continue
            except Exception as exc:
                report.errors += 1
                report.details.append(f"{job_id}: {type(exc).__name__}")
                logger.exception("Reconciliation failed for job %s", job_id)
                continue
            if outcome:
                report.enqueued += 1
            else:
                report.skipped += 1

        self._last_report = report
        if report.manifests:
            logger.info(
                "Reconciliation (%s): manifests=%s enqueued=%s skipped=%s errors=%s",
                trigger,
                report.manifests,
                report.enqueued,
                report.skipped,
                report.errors,
            )
        return report

    def _job_id_from_key(self, key: str) -> str | None:
        prefix = self._settings.jobs_prefix
        suffix = f"/{self._settings.fetch_manifest_filename}"
        if not key.startswith(prefix) or not key.endswith(suffix):
            return None
        job_id = key[len(prefix) : -len(suffix)]
        if not job_id or "/" in job_id:
            return None
        try:
            return validate_job_id(job_id)
        except AppError:
            logger.warning("Ignoring manifest with invalid jobId: %s", key)
            return None

    async def _maybe_enqueue(self, job_id: str, manifest_key: str, *, retry_failed: bool) -> bool:
        raw = await self._objects.get_bytes(manifest_key)
        manifest = FetchManifest.model_validate_json(raw)
        if manifest.job_id != job_id:
            logger.warning("Manifest jobId mismatch for key %s", manifest_key)
            return False
        if manifest.status == "FAILED":
            return False
        if not manifest.images:
            return False

        if self._queue.is_active(job_id):
            return False

        status = await self._read_status(job_id)
        if status is not None:
            state = status.effective_state
            if state == IngestionState.INDEXED:
                return False
            if state == IngestionState.FAILED and not retry_failed:
                return False
            # Bounded automatic recovery: every attempt increments the persisted
            # counter, so a permanent failure stops being retried by reconciliation.
            if status.attempts >= self._settings.reconciliation_max_attempts:
                logger.info(
                    "Skipping automatic retry for job %s: attempts=%s >= %s",
                    job_id,
                    status.attempts,
                    self._settings.reconciliation_max_attempts,
                )
                return False

        result = await self._queue.enqueue(job_id, manifest_key=manifest_key)
        return result.enqueued

    async def _read_status(self, job_id: str) -> IngestionStatus | None:
        cached = self._pipeline.cached_status(job_id)
        if cached is not None:
            return cached
        return await self._pipeline.read_persisted_status(job_id)
