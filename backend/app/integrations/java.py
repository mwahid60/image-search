"""HTTP client for the Java Spring Boot fetcher service."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config import Settings
from app.errors import UpstreamError, UpstreamTimeoutError
from app.logging_config import get_logger

logger = get_logger(__name__)


class FetchJobAccepted(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    job_id: str
    status: str = "FETCHING"


class JavaFetchJob(BaseModel):
    """Lenient view of the Java job status payload (extra fields are ignored)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    job_id: str | None = None
    # Java FetchJobView exposes `fetchStatus`, not `status`.
    fetch_status: str | None = None
    requested_classes: list[str] = Field(default_factory=list)
    images_per_class: int | None = None
    downloaded: int | None = None
    failed_downloads: int | None = None
    duplicates: int | None = None
    manifest_key: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class JavaFetcherClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.java_base_url,
            timeout=httpx.Timeout(settings.java_http_timeout_seconds),
        )

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def create_fetch_job(self, classes: list[str], images_per_class: int) -> FetchJobAccepted:
        payload = {"classes": classes, "imagesPerClass": images_per_class}
        try:
            response = await self._client.post("/api/v1/fetch-jobs", json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(
                "Java fetcher did not respond in time",
                code="JAVA_TIMEOUT",
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("Java fetcher is unavailable", code="JAVA_UNAVAILABLE") from exc

        if response.status_code in {400, 409, 422}:
            raise UpstreamError(
                "Java fetcher rejected the fetch request",
                code="JAVA_REJECTED_REQUEST",
                status_code=400,
                details=_safe_error_details(response),
            )
        if response.status_code >= 500:
            raise UpstreamError(
                "Java fetcher failed to create the fetch job",
                code="JAVA_ERROR",
                details=_safe_error_details(response),
            )
        if response.status_code >= 300:
            raise UpstreamError(
                f"Unexpected Java fetcher status {response.status_code}",
                code="JAVA_UNEXPECTED_STATUS",
                details=_safe_error_details(response),
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(
                "Java fetcher returned an invalid JSON response", code="JAVA_INVALID_RESPONSE"
            ) from exc
        try:
            return FetchJobAccepted.model_validate(body)
        except Exception as exc:
            raise UpstreamError(
                "Java fetcher response is missing jobId", code="JAVA_INVALID_RESPONSE"
            ) from exc

    async def get_fetch_job(self, job_id: str) -> JavaFetchJob | None:
        try:
            response = await self._client.get(f"/api/v1/fetch-jobs/{job_id}")
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError("Java fetcher status request timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("Java fetcher is unavailable", code="JAVA_UNAVAILABLE") from exc

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise UpstreamError(
                f"Java fetcher returned status {response.status_code}", code="JAVA_ERROR"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError(
                "Java fetcher returned an invalid JSON response", code="JAVA_INVALID_RESPONSE"
            ) from exc
        if not isinstance(body, dict):
            raise UpstreamError(
                "Java fetcher returned an unexpected payload", code="JAVA_INVALID_RESPONSE"
            )
        return JavaFetchJob.model_validate(body)

    async def close(self) -> None:
        await self._client.aclose()


def _safe_error_details(response: httpx.Response) -> dict[str, Any]:
    """Return a small, non-secret error detail payload from the upstream response."""
    try:
        body = response.json()
    except ValueError:
        text = response.text[:300]
        return {"upstreamStatus": response.status_code, "upstreamBody": text}
    if isinstance(body, dict):
        return {"upstreamStatus": response.status_code, "upstreamBody": body}
    return {"upstreamStatus": response.status_code}
