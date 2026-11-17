"""Structured application errors shared by API routes and integrations."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error rendered as a structured JSON error by the API layer."""

    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return {"error": payload}


class InvalidRequestError(AppError):
    status_code = 400
    code = "INVALID_REQUEST"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ManifestError(AppError):
    status_code = 422
    code = "INVALID_MANIFEST"


class UpstreamError(AppError):
    status_code = 502
    code = "UPSTREAM_ERROR"


class UpstreamTimeoutError(UpstreamError):
    status_code = 504
    code = "UPSTREAM_TIMEOUT"


class EmbeddingError(UpstreamError):
    code = "EMBEDDING_ERROR"


class VectorStoreError(AppError):
    status_code = 503
    code = "VECTOR_STORE_UNAVAILABLE"


class ObjectStoreError(AppError):
    status_code = 503
    code = "OBJECT_STORE_UNAVAILABLE"


class ObjectNotFoundError(ObjectStoreError):
    status_code = 404
    code = "OBJECT_NOT_FOUND"


class NotReadyError(AppError):
    status_code = 503
    code = "NOT_READY"
