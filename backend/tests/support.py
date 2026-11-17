"""In-memory fakes and helpers for tests (no external services required)."""

from __future__ import annotations

import hashlib
import io
import math
import random
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.errors import ObjectNotFoundError, UpstreamError
from app.ingestion.queue import EnqueueResult
from app.integrations.java import FetchJobAccepted, JavaFetchJob
from app.models.records import ImageReference, ImageSearchHit, ImageVectorRecord
from PIL import Image

FIXED_NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "background_tasks_enabled": False,
        "sse_enabled": False,
        "reconciliation_enabled": False,
        "sse_reconnect_initial_seconds": 0.01,
        "sse_reconnect_max_seconds": 0.05,
        "java_base_url": "http://java.test",
        "llama_base_url": "http://llama.test",
        "minio_endpoint": "minio.test:9000",
        "minio_access_key": "test-access",
        "minio_secret_key": "test-secret",
        "milvus_db_path": "data/test-milvus.db",
        "embedding_dimension": 768,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def png_bytes(color: str = "red", size: tuple[int, int] = (16, 12)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_document(
    job_id: str = "fetch-test-001",
    images: Sequence[dict[str, Any]] | None = None,
    status: str = "COMPLETED",
    failed_downloads: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "status": status,
        "requestedClasses": ["dog", "cat"],
        "images": list(images or []),
        "failedDownloads": list(failed_downloads or []),
        "startedAt": "2026-11-16T11:00:00Z",
        "completedAt": "2026-11-16T11:05:00Z",
    }


def manifest_image(
    data: bytes,
    *,
    object_key: str | None = None,
    image_id: str | None = None,
    sha256: str | None = None,
    content_type: str = "image/png",
    title: str = "A dog in the snow",
    requested_class: str = "dog",
    tags: Sequence[str] = ("dog", "snow"),
) -> dict[str, Any]:
    digest = sha(data)
    return {
        "imageId": image_id or digest,
        "objectKey": object_key or f"images/bank-mas/{digest}.png",
        "contentType": content_type,
        "sha256": sha256 or digest,
        "width": 16,
        "height": 12,
        "sourceUrl": "https://commons.wikimedia.org/wiki/File:Dog.png",
        "license": "cc-by",
        "creator": "Test Creator",
        "evaluationMetadata": {
            "openverseId": "ov-1",
            "requestedClass": requested_class,
            "title": title,
            "tags": list(tags),
        },
    }


class FakeObjectStore:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.puts: list[tuple[str, bytes, str]] = []
        self.fail_ping: Exception | None = None
        self.bucket_ready = False

    async def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise ObjectNotFoundError(f"missing object {key}")
        return self.objects[key]

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self.objects[key] = data
        self.puts.append((key, data, content_type))

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def list_keys(self, prefix: str) -> list[str]:
        return sorted(key for key in self.objects if key.startswith(prefix))

    async def ensure_bucket(self) -> None:
        self.bucket_ready = True

    async def ping(self) -> None:
        if self.fail_ping is not None:
            raise self.fail_ping

    async def close(self) -> None:
        return None


class FakeEmbeddings:
    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension
        self.image_calls: list[tuple[bytes, str]] = []
        self.text_calls: list[str] = []
        self.fail_ping: Exception | None = None
        self.fail_image: Exception | None = None
        self.fail_text: Exception | None = None

    def _vector(self, seed: bytes) -> list[float]:
        rng = random.Random(int.from_bytes(seed[:8], "big"))
        return [rng.uniform(-1, 1) for _ in range(self.dimension)]

    async def embed_image(self, image_bytes: bytes, content_type: str) -> list[float]:
        self.image_calls.append((image_bytes, content_type))
        if self.fail_image is not None:
            raise self.fail_image
        return self._vector(hashlib.sha256(image_bytes).digest())

    async def embed_text(self, query: str) -> list[float]:
        self.text_calls.append(query)
        if self.fail_text is not None:
            raise self.fail_text
        return self._vector(hashlib.sha256(query.encode()).digest())

    async def ping(self) -> None:
        if self.fail_ping is not None:
            raise self.fail_ping

    async def close(self) -> None:
        return None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = math.fsum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(math.fsum(a * a for a in left))
    right_norm = math.sqrt(math.fsum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class FakeVectorStore:
    def __init__(self) -> None:
        self.records: dict[str, ImageVectorRecord] = {}
        self.upsert_batches: list[list[ImageVectorRecord]] = []
        self.fail_ping: Exception | None = None
        self.fail_upsert: Exception | None = None
        self.search_calls: list[tuple[list[float], int]] = []

    async def ensure_ready(self) -> None:
        return None

    async def upsert(self, records: Sequence[ImageVectorRecord]) -> None:
        if self.fail_upsert is not None:
            raise self.fail_upsert
        batch = list(records)
        self.upsert_batches.append(batch)
        for record in batch:
            self.records[record.image_id] = record

    async def existing_created_at(self, image_ids: Iterable[str]) -> dict[str, int]:
        return {
            image_id: self.records[image_id].created_at
            for image_id in image_ids
            if image_id in self.records
        }

    async def search(self, vector: Sequence[float], top_k: int) -> list[ImageSearchHit]:
        self.search_calls.append((list(vector), top_k))
        scored = [(record, _cosine(vector, record.embedding)) for record in self.records.values()]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [
            ImageSearchHit(
                image_id=record.image_id,
                score=score,
                object_key=record.object_key,
                content_type=record.content_type,
                width=record.width,
                height=record.height,
                sha256=record.sha256,
                source_url=record.source_url,
                license=record.license,
                creator=record.creator,
            )
            for record, score in scored[:top_k]
        ]

    async def get_by_id(self, image_id: str) -> ImageReference | None:
        record = self.records.get(image_id)
        if record is None:
            return None
        return ImageReference(
            image_id=record.image_id,
            object_key=record.object_key,
            content_type=record.content_type,
            sha256=record.sha256,
        )

    async def count(self) -> int:
        return len(self.records)

    async def ping(self) -> None:
        if self.fail_ping is not None:
            raise self.fail_ping

    async def close(self) -> None:
        return None


class FakeJavaClient:
    def __init__(self) -> None:
        self.create_calls: list[tuple[list[str], int]] = []
        self.create_error: Exception | None = None
        self.status_job: JavaFetchJob | None = None
        self.status_error: Exception | None = None
        self.status_calls: list[str] = []

    async def create_fetch_job(self, classes: list[str], images_per_class: int) -> FetchJobAccepted:
        self.create_calls.append((classes, images_per_class))
        if self.create_error is not None:
            raise self.create_error
        return FetchJobAccepted(jobId="fetch-java-001", status="FETCHING")

    async def get_fetch_job(self, job_id: str) -> JavaFetchJob | None:
        self.status_calls.append(job_id)
        if self.status_error is not None:
            raise self.status_error
        return self.status_job

    async def close(self) -> None:
        return None


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, bool]] = []

    async def enqueue(
        self, job_id: str, *, manifest_key: str | None = None, force: bool = False
    ) -> EnqueueResult:
        self.enqueued.append((job_id, force))
        return EnqueueResult(job_id, True, "enqueued")

    def is_active(self, job_id: str) -> bool:
        return False


class MutableClock:
    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self.current = self.current + timedelta(seconds=seconds)


def java_status(job_id: str = "fetch-test-001", status: str = "COMPLETED") -> JavaFetchJob:
    return JavaFetchJob(
        jobId=job_id,
        fetchStatus=status,
        requestedClasses=["dog", "cat"],
        imagesPerClass=2,
        downloaded=1,
        failedDownloads=0,
        duplicates=0,
        manifestKey=None,
        startedAt="2026-11-16T11:00:00Z",
        completedAt=None,
        error=None,
    )


def upstream_rejected() -> UpstreamError:
    return UpstreamError(
        "Java fetcher rejected the fetch request",
        code="JAVA_REJECTED_REQUEST",
        status_code=400,
        details={"upstreamStatus": 400},
    )
