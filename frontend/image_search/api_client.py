"""Small typed client for the Python FastAPI backend.

The Streamlit app talks only to this API. No direct access to Java, Openverse,
MinIO, Milvus, or llama.cpp happens from the frontend.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote

import httpx

from .config import normalize_base_url

MAX_ERROR_DETAIL_CHARS = 400


class ApiError(Exception):
    """Base class for failures returned by or while calling the API."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ApiTimeoutError(ApiError):
    """The API did not answer within the configured timeout."""


class ApiConnectionError(ApiError):
    """The API could not be reached (DNS, refused connection, ...)."""


class MalformedResponseError(ApiError):
    """The API answered with a payload outside the documented contract."""


@dataclass(frozen=True, slots=True)
class FetchJobCreated:
    job_id: str
    status: str


@dataclass(frozen=True, slots=True)
class FetchJobStatus:
    """Aggregated fetch/ingestion status from ``GET /api/v1/fetch-jobs/{id}``."""

    job_id: str
    fetch_status: str
    ingestion_status: str | None = None
    downloaded: int | None = None
    failed_downloads: int | None = None
    indexed: int | None = None
    duplicates: int | None = None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IngestionRetry:
    """Result of ``POST /api/v1/ingest/{id}``.

    The contract does not require a response body, so ``status`` may be
    ``None`` when the API accepted the retry without echoing a status.
    """

    job_id: str
    status: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    image_id: str
    image_url: str
    score: float | None = None
    content_type: str | None = None
    source_url: str | None = None
    license: str | None = None
    creator: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    results: tuple[SearchResultItem, ...]


def _malformed(path: str, detail: str) -> MalformedResponseError:
    return MalformedResponseError(f"Respons {path} tidak sesuai kontrak API: {detail}.")


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _malformed(path, "body harus berupa objek JSON")
    return value


def _required_str(data: Mapping[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _malformed(path, f"field '{key}' harus berupa string tidak kosong")
    return value.strip()


def _optional_str(data: Mapping[str, Any], key: str, path: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _malformed(path, f"field '{key}' harus berupa string atau null")
    cleaned = value.strip()
    return cleaned or None


def _optional_number(data: Mapping[str, Any], key: str, path: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _malformed(path, f"field '{key}' harus berupa angka atau null")
    number = float(value)
    if not math.isfinite(number):
        raise _malformed(path, f"field '{key}' tidak boleh NaN atau infinity")
    return number


def _optional_count(data: Mapping[str, Any], key: str, path: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    # ``failedDownloads`` is documented as a number, but accepting a list lets
    # the UI stay truthful if the backend sends the detailed failed list.
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _malformed(path, f"field '{key}' harus berupa bilangan bulat >= 0")
    if value < 0:
        raise _malformed(path, f"field '{key}' tidak boleh negatif")
    return value


def _format_ingestion_error(data: Mapping[str, Any], path: str) -> str:
    """Turn an IngestionError object into one human-readable line.

    Only the ingestion fields are read. Textual evaluation metadata that may
    travel with an error object is deliberately ignored and never rendered.
    """

    stage = _optional_str(data, "stage", path)
    reason = _optional_str(data, "reason", path)
    image_id = _optional_str(data, "imageId", path)
    object_key = _optional_str(data, "objectKey", path)

    identifiers: list[str] = []
    if image_id:
        identifiers.append(f"imageId={image_id}")
    if object_key:
        identifiers.append(f"objectKey={object_key}")

    if stage and reason:
        message = f"Gagal di tahap {stage}: {reason}"
    elif stage:
        message = f"Gagal di tahap {stage}"
    elif reason:
        message = f"Gagal: {reason}"
    elif identifiers:
        message = "Gagal memproses gambar"
    else:
        message = "Error ingestion tanpa detail"

    if identifiers:
        message += f" ({', '.join(identifiers)})"
    return message


def _error_list(data: Mapping[str, Any], key: str, path: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _malformed(path, f"field '{key}' harus berupa array")

    messages: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path} {key}[{index}]"
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                messages.append(cleaned)
        elif isinstance(item, Mapping):
            messages.append(_format_ingestion_error(item, item_path))
        else:
            raise _malformed(
                item_path,
                "setiap item harus berupa string atau objek IngestionError",
            )
    return tuple(messages)


def _error_detail(response: httpx.Response) -> str:
    payload: Any = None
    try:
        payload = response.json()
    except ValueError:
        payload = None

    detail: Any = None
    if isinstance(payload, Mapping):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")

    message = ""
    if isinstance(detail, str):
        message = detail.strip()
    elif isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, Mapping):
                item_message = item.get("msg") or item.get("message")
                if not isinstance(item_message, str):
                    continue
                location = item.get("loc")
                if isinstance(location, (list, tuple)) and location:
                    prefix = ".".join(str(part) for part in location)
                    parts.append(f"{prefix}: {item_message}")
                else:
                    parts.append(item_message)
        message = "; ".join(part for part in parts if part)

    if not message:
        message = "permintaan ditolak"

    if len(message) > MAX_ERROR_DETAIL_CHARS:
        message = f"{message[:MAX_ERROR_DETAIL_CHARS]}…"
    return f"{message} (HTTP {response.status_code})"


def _parse_search_result(item: Any, path: str) -> SearchResultItem:
    data = _require_mapping(item, path)
    return SearchResultItem(
        image_id=_required_str(data, "imageId", path),
        image_url=_required_str(data, "imageUrl", path),
        score=_optional_number(data, "score", path),
        content_type=_optional_str(data, "contentType", path),
        source_url=_optional_str(data, "sourceUrl", path),
        license=_optional_str(data, "license", path),
        creator=_optional_str(data, "creator", path),
    )


class ApiClient:
    """HTTP client for the Python API, with timeout and payload validation."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout: float = 30.0,
        search_timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.request_timeout = request_timeout
        self.search_timeout = search_timeout or request_timeout
        self._http = httpx.Client(
            timeout=request_timeout,
            headers={"Accept": "application/json"},
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        effective_timeout = self.request_timeout if timeout is None else timeout
        try:
            response = self._http.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                timeout=effective_timeout,
            )
        except httpx.TimeoutException as exc:
            raise ApiTimeoutError(
                f"Permintaan {method} {path} melewati batas waktu "
                f"{effective_timeout:g} detik."
            ) from exc
        except httpx.RequestError as exc:
            raise ApiConnectionError(
                f"Tidak dapat terhubung ke Python API di {self.base_url}."
            ) from exc

        if response.status_code >= 300:
            raise ApiError(_error_detail(response), status_code=response.status_code)
        return response

    def _json_body(self, response: httpx.Response, path: str) -> Mapping[str, Any]:
        if not response.content:
            raise _malformed(path, "body kosong")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise _malformed(path, "body bukan JSON yang valid") from exc
        return _require_mapping(payload, path)

    def create_fetch_job(
        self, classes: Sequence[str], images_per_class: int
    ) -> FetchJobCreated:
        """Start a fetch job via ``POST /api/v1/fetch-jobs``."""

        path = "/api/v1/fetch-jobs"
        response = self._request(
            "POST",
            path,
            json_body={
                "classes": list(classes),
                "imagesPerClass": images_per_class,
            },
        )
        data = self._json_body(response, path)
        return FetchJobCreated(
            job_id=_required_str(data, "jobId", path),
            status=_required_str(data, "status", path),
        )

    def get_fetch_job(self, job_id: str) -> FetchJobStatus:
        """Read the aggregated status via ``GET /api/v1/fetch-jobs/{jobId}``."""

        path = f"/api/v1/fetch-jobs/{quote(job_id, safe='')}"
        response = self._request("GET", path)
        data = self._json_body(response, path)
        return FetchJobStatus(
            job_id=_required_str(data, "jobId", path),
            fetch_status=_required_str(data, "fetchStatus", path),
            ingestion_status=_optional_str(data, "ingestionStatus", path),
            downloaded=_optional_count(data, "downloaded", path),
            failed_downloads=_optional_count(data, "failedDownloads", path),
            indexed=_optional_count(data, "indexed", path),
            duplicates=_optional_count(data, "duplicates", path),
            errors=_error_list(data, "errors", path),
        )

    def retry_ingestion(self, job_id: str) -> IngestionRetry:
        """Trigger ``POST /api/v1/ingest/{jobId}`` for recovery."""

        path = f"/api/v1/ingest/{quote(job_id, safe='')}"
        response = self._request("POST", path)

        status: str | None = None
        if response.content:
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                payload = None
            if isinstance(payload, Mapping):
                raw_status = payload.get("status")
                if isinstance(raw_status, str) and raw_status.strip():
                    status = raw_status.strip()

        return IngestionRetry(job_id=job_id, status=status)

    def search(self, query: str, top_k: int) -> SearchResponse:
        """Run a vector search via ``POST /api/v1/search``."""

        path = "/api/v1/search"
        response = self._request(
            "POST",
            path,
            json_body={"query": query, "topK": top_k},
            timeout=self.search_timeout,
        )
        data = self._json_body(response, path)

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise _malformed(path, "field 'results' harus berupa array")

        results = tuple(
            _parse_search_result(item, f"{path} results[{index}]")
            for index, item in enumerate(raw_results)
        )
        return SearchResponse(
            query=_optional_str(data, "query", path) or query,
            results=results,
        )
