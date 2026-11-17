"""Small shared utilities (hashing, vector math, time, job-id validation)."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.errors import EmbeddingError, InvalidRequestError

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def isoformat(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_job_id(job_id: str) -> str:
    if not _JOB_ID_PATTERN.match(job_id or ""):
        raise InvalidRequestError(
            "jobId must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            code="INVALID_JOB_ID",
        )
    return job_id


def validate_image_id(image_id: str) -> str:
    if not _HEX64_PATTERN.match((image_id or "").lower()):
        raise InvalidRequestError(
            "imageId must be a SHA-256 hex string (64 lowercase hex characters)",
            code="INVALID_IMAGE_ID",
        )
    return image_id.lower()


def is_hex64(value: str | None) -> bool:
    return bool(value) and bool(_HEX64_PATTERN.match(value))  # type: ignore[arg-type]


def sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def l2_normalize(vector: Sequence[float], *, dimension: int | None = None) -> list[float]:
    """Return the L2-normalized vector; raise EmbeddingError for invalid input."""
    if not vector:
        raise EmbeddingError("Embedding vector is empty", code="INVALID_EMBEDDING")
    if dimension is not None and len(vector) != dimension:
        raise EmbeddingError(
            f"Embedding dimension mismatch: expected {dimension}, got {len(vector)}",
            code="INVALID_EMBEDDING_DIMENSION",
        )
    values: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise EmbeddingError("Embedding contains a non-numeric value", code="INVALID_EMBEDDING")
        number = float(item)
        if not math.isfinite(number):
            raise EmbeddingError(
                "Embedding contains NaN or infinity", code="INVALID_EMBEDDING_VALUE"
            )
        values.append(number)
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise EmbeddingError("Embedding vector has zero or invalid norm", code="INVALID_EMBEDDING")
    return [value / norm for value in values]


class Clock:
    """Injectable clock so tests do not depend on wall time."""

    def __init__(self, now: Callable[[], datetime] = utc_now) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now()

    def iso_now(self) -> str:
        return isoformat(self.now())
