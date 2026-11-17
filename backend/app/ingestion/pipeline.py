"""Idempotent ingestion pipeline: manifest -> MinIO image -> embedding -> Milvus."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.errors import AppError, ManifestError, ObjectStoreError
from app.ingestion.images import (
    ImageValidationError,
    inspect_image,
    prepare_embedding_image,
)
from app.integrations.embeddings import EmbeddingsClient
from app.integrations.object_store import ObjectStore
from app.integrations.vector_store import VectorStore
from app.logging_config import get_logger
from app.models.ingestion import IngestionError, IngestionState, IngestionStatus
from app.models.manifest import FetchManifest, ManifestImage
from app.models.records import ImageVectorRecord
from app.utils import Clock, is_hex64, l2_normalize, sha256_hex

logger = get_logger(__name__)

_JSON_CONTENT_TYPE = "application/json"


class _ImageFailure(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


class IngestionPipeline:
    """Single-worker-friendly ingestion service.

    - deterministic `image_id` = SHA-256 of image bytes
    - idempotent Milvus upsert, `created_at` preserved on re-ingestion
    - status persisted to `jobs/bank-mas/{jobId}/ingestion-status.json`
    - per-image failures never abort the whole job
    """

    def __init__(
        self,
        settings: Settings,
        object_store: ObjectStore,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._objects = object_store
        self._embeddings = embeddings
        self._vectors = vector_store
        self._clock = clock or Clock()
        self._job_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._status_cache: dict[str, IngestionStatus] = {}

    # ------------------------------------------------------------------ helpers
    def cached_status(self, job_id: str) -> IngestionStatus | None:
        return self._status_cache.get(job_id)

    async def read_persisted_status(self, job_id: str) -> IngestionStatus | None:
        key = self._settings.ingestion_status_key(job_id)
        try:
            raw = await self._objects.get_bytes(key)
        except ObjectStoreError as exc:
            if exc.status_code == 404:
                return None
            raise
        try:
            return IngestionStatus.model_validate_json(raw)
        except ValidationError:
            logger.warning("Ignoring malformed ingestion status for job %s", job_id)
            return None

    async def _job_lock(self, job_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._job_locks.setdefault(job_id, asyncio.Lock())

    async def _read_manifest(self, job_id: str, manifest_key: str | None) -> FetchManifest:
        key = manifest_key or self._settings.manifest_key(job_id)
        expected_key = self._settings.manifest_key(job_id)
        if key != expected_key:
            raise ManifestError(
                "manifestKey does not match the required jobs/bank-mas/{jobId}/ prefix",
                code="INVALID_MANIFEST_KEY",
            )
        try:
            raw = await self._objects.get_bytes(key)
        except ObjectStoreError as exc:
            if exc.status_code == 404:
                raise ManifestError(
                    f"fetch manifest not found for job {job_id}",
                    code="MANIFEST_NOT_FOUND",
                    status_code=404,
                ) from exc
            raise
        try:
            manifest = FetchManifest.model_validate_json(raw)
        except ValidationError as exc:
            raise ManifestError(
                "fetch manifest is not valid", details={"errors": exc.error_count()}
            ) from exc
        if manifest.job_id != job_id:
            raise ManifestError(
                "fetch manifest jobId does not match requested job",
                code="MANIFEST_JOB_MISMATCH",
            )
        return manifest

    async def _previous_status(self, job_id: str) -> IngestionStatus | None:
        cached = self._status_cache.get(job_id)
        if cached is not None:
            return cached
        try:
            return await self.read_persisted_status(job_id)
        except AppError as exc:
            logger.warning(
                "Could not read previous ingestion status for %s: %s",
                job_id,
                exc.message,
            )
            return None

    async def _write_status(self, status: IngestionStatus) -> None:
        key = self._settings.ingestion_status_key(status.job_id)
        payload = json.dumps(
            status.model_dump(by_alias=True, mode="json"), ensure_ascii=False
        ).encode("utf-8")
        await self._objects.put_bytes(key, payload, _JSON_CONTENT_TYPE)
        self._status_cache[status.job_id] = status

    # ------------------------------------------------------------------ ingestion
    async def ingest_job(self, job_id: str, manifest_key: str | None = None) -> IngestionStatus:
        lock = await self._job_lock(job_id)
        async with lock:
            try:
                return await self._ingest(job_id, manifest_key)
            except AppError as exc:
                logger.error("Ingestion of job %s failed: %s", job_id, exc.message)
                return await self._record_failure(job_id, exc.code, exc.message)
            except Exception as exc:
                logger.exception("Unexpected ingestion failure for job %s", job_id)
                return await self._record_failure(
                    job_id, "INGESTION_ERROR", f"{type(exc).__name__}: {exc}"
                )

    async def _record_failure(self, job_id: str, stage: str, reason: str) -> IngestionStatus:
        cached = self._status_cache.get(job_id)
        status = IngestionStatus(
            job_id=job_id,
            status=IngestionState.FAILED,
            errors=[IngestionError(stage=stage, reason=reason)],
            failed=1,
            attempts=cached.attempts if cached else 1,
            total_images=cached.total_images if cached else 0,
            started_at=cached.started_at if cached else self._clock.iso_now(),
            completed_at=self._clock.iso_now(),
        )
        try:
            await self._write_status(status)
        except Exception:
            logger.exception("Failed to persist ingestion failure status for %s", job_id)
            self._status_cache[job_id] = status
        return status

    async def _ingest(self, job_id: str, manifest_key: str | None) -> IngestionStatus:
        started_at = self._clock.iso_now()
        previous = await self._previous_status(job_id)
        status = IngestionStatus(
            job_id=job_id,
            status=IngestionState.INGESTING,
            attempts=(previous.attempts if previous else 0) + 1,
            started_at=started_at,
        )
        self._status_cache[job_id] = status
        await self._write_status(status)

        manifest = await self._read_manifest(job_id, manifest_key)
        total_images = len(manifest.images)
        status = status.model_copy(update={"total_images": total_images})
        await self._write_status(status)

        if manifest.status not in {"COMPLETED", "PARTIAL"}:
            return await self._finish(
                status,
                indexed=0,
                duplicates=0,
                errors=[
                    IngestionError(
                        stage="manifest",
                        reason=f"manifest status {manifest.status!r} is not ingestable",
                    )
                ],
            )
        if not manifest.images:
            return await self._finish(
                status,
                indexed=0,
                duplicates=0,
                errors=[IngestionError(stage="manifest", reason="manifest has no images")],
            )

        candidate_ids = self._candidate_ids(manifest.images)
        existing: dict[str, int] = {}
        if candidate_ids:
            existing = await self._vectors.existing_created_at(candidate_ids)

        indexed = 0
        duplicates = 0
        errors: list[IngestionError] = []
        seen_in_job: set[str] = set()
        batch_size = self._settings.ingest_batch_size

        for start in range(0, total_images, batch_size):
            batch = manifest.images[start : start + batch_size]
            records: list[ImageVectorRecord] = []
            for image in batch:
                try:
                    duplicate = self._is_duplicate(image, existing, seen_in_job)
                    if duplicate:
                        image_id = self._resolve_image_id(image)
                        seen_in_job.add(image_id)
                        duplicates += 1
                        logger.info("Skipping duplicate image %s in job %s", image_id, job_id)
                        continue
                    record = await self._process_image(image, existing)
                    records.append(record)
                    seen_in_job.add(record.image_id)
                    indexed += 1
                except _ImageFailure as exc:
                    errors.append(
                        IngestionError(
                            image_id=image.image_id,
                            object_key=image.object_key,
                            stage=exc.stage,
                            reason=exc.reason,
                        )
                    )
                except Exception as exc:
                    logger.exception("Unexpected failure ingesting image in job %s", job_id)
                    errors.append(
                        IngestionError(
                            image_id=image.image_id,
                            object_key=image.object_key,
                            stage="unexpected",
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
            if records:
                try:
                    await self._vectors.upsert(records)
                except Exception as exc:
                    logger.exception("Milvus upsert failed for job %s", job_id)
                    for record in records:
                        indexed -= 1
                        seen_in_job.discard(record.image_id)
                        errors.append(
                            IngestionError(
                                image_id=record.image_id,
                                object_key=record.object_key,
                                stage="upsert",
                                reason=f"{type(exc).__name__}: {exc}",
                            )
                        )

        return await self._finish(status, indexed=indexed, duplicates=duplicates, errors=errors)

    def _candidate_ids(self, images: Sequence[ManifestImage]) -> list[str]:
        ids: list[str] = []
        for image in images:
            image_id = self._resolve_image_id(image)
            if image_id:
                ids.append(image_id)
        return list(dict.fromkeys(ids))

    @staticmethod
    def _resolve_image_id(image: ManifestImage) -> str:
        for value in (image.sha256, image.image_id):
            if is_hex64(value):
                return value.lower()  # type: ignore[union-attr]
        return ""

    def _is_duplicate(
        self, image: ManifestImage, existing: dict[str, int], seen_in_job: set[str]
    ) -> bool:
        image_id = self._resolve_image_id(image)
        return bool(image_id) and (image_id in existing or image_id in seen_in_job)

    async def _process_image(
        self, image: ManifestImage, existing: dict[str, int]
    ) -> ImageVectorRecord:
        if not self._settings.is_image_key(image.object_key):
            raise _ImageFailure(
                "validate",
                f"objectKey is outside the required prefix {self._settings.image_prefix}",
            )
        try:
            data = await self._objects.get_bytes(image.object_key)
        except AppError as exc:
            raise _ImageFailure("fetch", exc.message) from exc
        if len(data) > self._settings.ingest_max_image_bytes:
            raise _ImageFailure(
                "validate",
                f"image exceeds max size of {self._settings.ingest_max_image_bytes} bytes",
            )
        try:
            info = inspect_image(data, max_pixels=self._settings.ingest_max_image_pixels)
            embedding_bytes, embedding_mime = prepare_embedding_image(
                data,
                info.mime_type,
                max_dimension=self._settings.ingest_embedding_max_dimension,
            )
        except ImageValidationError as exc:
            raise _ImageFailure("validate", str(exc)) from exc

        computed_sha = sha256_hex(data)
        if image.sha256 and not is_hex64(image.sha256):
            raise _ImageFailure("validate", "manifest sha256 is not a 64-char hex digest")
        if image.sha256 and image.sha256.lower() != computed_sha:
            raise _ImageFailure("validate", "sha256 mismatch between manifest and MinIO object")
        if not is_hex64(image.image_id):
            raise _ImageFailure("validate", "manifest imageId is not a SHA-256 hex digest")
        if image.image_id.lower() != computed_sha:
            raise _ImageFailure("validate", "imageId does not match the SHA-256 of the object")

        declared_mime = (image.content_type or "").split(";")[0].strip().lower()
        if declared_mime and not declared_mime.startswith("image/"):
            raise _ImageFailure("validate", "manifest contentType is not an image MIME type")

        image_id = computed_sha
        try:
            vector = await self._embeddings.embed_image(embedding_bytes, embedding_mime)
            embedding = l2_normalize(vector, dimension=self._settings.embedding_dimension)
        except AppError as exc:
            raise _ImageFailure("embedding", exc.message) from exc

        created_at = existing.get(image_id) or int(self._clock.now().timestamp())
        return ImageVectorRecord(
            image_id=image_id,
            embedding=embedding,
            object_key=image.object_key,
            content_type=info.mime_type,
            width=image.width or info.width,
            height=image.height or info.height,
            sha256=computed_sha,
            source_url=image.source_url or "",
            license=image.license or "",
            creator=image.creator or "",
            # Copied verbatim for offline evaluation only; never sent to the model.
            evaluation_metadata=dict(image.evaluation_metadata or {}),
            created_at=created_at,
        )

    async def _finish(
        self,
        status: IngestionStatus,
        *,
        indexed: int,
        duplicates: int,
        errors: list[IngestionError],
    ) -> IngestionStatus:
        usable = indexed + duplicates
        if usable == 0:
            final_state = IngestionState.FAILED
        elif errors:
            final_state = IngestionState.PARTIAL
        else:
            final_state = IngestionState.INDEXED
        final = status.model_copy(
            update={
                "status": final_state,
                "indexed": indexed,
                "duplicates": duplicates,
                "failed": len(errors),
                "errors": errors,
                "completed_at": self._clock.iso_now(),
            }
        )
        await self._write_status(final)
        logger.info(
            "Ingestion finished for job %s: status=%s indexed=%s duplicates=%s failed=%s",
            final.job_id,
            final.status,
            final.indexed,
            final.duplicates,
            final.failed,
        )
        return final


def manifest_preview(manifest: FetchManifest) -> dict[str, Any]:
    """Small, evaluation-metadata-free summary used for logging."""
    return {
        "jobId": manifest.job_id,
        "status": manifest.status,
        "images": len(manifest.images),
        "failedDownloads": len(manifest.failed_downloads),
    }
