"""Aggregated fetch job status (Java runtime + manifest + ingestion status)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.config import Settings
from app.errors import AppError, NotFoundError, ObjectStoreError
from app.ingestion.pipeline import IngestionPipeline
from app.integrations.java import JavaFetcherClient, JavaFetchJob
from app.integrations.object_store import ObjectStore
from app.logging_config import get_logger
from app.models.ingestion import IngestionError, IngestionState, IngestionStatus
from app.models.manifest import CamelModel, FetchManifest
from app.utils import validate_job_id

logger = get_logger(__name__)


class FetchJobStatus(CamelModel):
    job_id: str
    fetch_status: str
    ingestion_status: str
    downloaded: int = 0
    failed_downloads: int = 0
    indexed: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[IngestionError] = Field(default_factory=list)
    manifest_key: str | None = None
    requested_classes: list[str] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    warnings: list[str] = Field(default_factory=list)


class FetchStatusService:
    def __init__(
        self,
        settings: Settings,
        java_client: JavaFetcherClient,
        object_store: ObjectStore,
        pipeline: IngestionPipeline,
    ) -> None:
        self._settings = settings
        self._java = java_client
        self._objects = object_store
        self._pipeline = pipeline

    async def get_status(self, job_id: str) -> FetchJobStatus:
        validate_job_id(job_id)
        warnings: list[str] = []

        java_job: JavaFetchJob | None = None
        try:
            java_job = await self._java.get_fetch_job(job_id)
        except AppError as exc:
            warnings.append(f"java status unavailable: {exc.code}")
            logger.warning("Java status unavailable for job %s: %s", job_id, exc.message)

        manifest = await self._read_manifest(job_id, warnings)
        ingestion = await self._read_ingestion_status(job_id)

        if java_job is None and manifest is None and ingestion is None:
            raise NotFoundError(f"fetch job {job_id} was not found", code="JOB_NOT_FOUND")

        fetch_status = self._fetch_status(java_job, manifest)
        ingestion_state = self._ingestion_status(manifest, ingestion)
        errors: list[IngestionError] = []
        if ingestion is not None:
            errors.extend(ingestion.errors)

        return FetchJobStatus(
            job_id=job_id,
            fetch_status=fetch_status,
            ingestion_status=ingestion_state,
            downloaded=len(manifest.images) if manifest else (java_job.downloaded or 0),
            failed_downloads=(
                len(manifest.failed_downloads) if manifest else (java_job.failed_downloads or 0)
            ),
            indexed=ingestion.indexed if ingestion else 0,
            duplicates=ingestion.duplicates if ingestion else 0,
            failed=ingestion.failed if ingestion else 0,
            errors=errors,
            manifest_key=self._settings.manifest_key(job_id) if manifest else None,
            requested_classes=(
                manifest.requested_classes if manifest else (java_job.requested_classes or [])
            ),
            started_at=manifest.started_at if manifest else None,
            completed_at=(manifest.completed_at or ingestion.completed_at) if manifest else None,
            warnings=warnings,
        )

    async def _read_manifest(self, job_id: str, warnings: list[str]) -> FetchManifest | None:
        key = self._settings.manifest_key(job_id)
        try:
            raw = await self._objects.get_bytes(key)
        except ObjectStoreError as exc:
            if exc.status_code != 404:
                warnings.append(f"manifest unavailable: {exc.code}")
                logger.warning("Manifest unavailable for job %s: %s", job_id, exc.message)
            return None
        try:
            manifest = FetchManifest.model_validate_json(raw)
        except Exception:
            warnings.append("manifest is malformed")
            logger.warning("Malformed manifest for job %s", job_id)
            return None
        if manifest.job_id != job_id:
            warnings.append("manifest jobId mismatch")
            return None
        return manifest

    async def _read_ingestion_status(self, job_id: str) -> IngestionStatus | None:
        cached = self._pipeline.cached_status(job_id)
        if cached is not None:
            return cached
        try:
            return await self._pipeline.read_persisted_status(job_id)
        except AppError as exc:
            logger.warning("Ingestion status unavailable for job %s: %s", job_id, exc.message)
            return None

    @staticmethod
    def _fetch_status(java_job: JavaFetchJob | None, manifest: FetchManifest | None) -> str:
        if java_job is not None and java_job.status:
            return java_job.status
        if manifest is not None:
            return manifest.status
        return "UNKNOWN"

    @staticmethod
    def _ingestion_status(manifest: FetchManifest | None, ingestion: IngestionStatus | None) -> str:
        if ingestion is not None:
            return ingestion.status.value
        if manifest is None:
            return "UNKNOWN"
        if manifest.status in {"COMPLETED", "PARTIAL"}:
            return IngestionState.PENDING.value
        return "UNKNOWN"


def fetch_status_payload(status: FetchJobStatus) -> dict[str, Any]:
    return status.model_dump(by_alias=True, mode="json")
