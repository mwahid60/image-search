"""Unit tests for formatting helpers."""

from __future__ import annotations

from image_search.api_client import (
    ApiConnectionError,
    ApiError,
    ApiTimeoutError,
    FetchJobStatus,
    MalformedResponseError,
)
from image_search.formatting import (
    available_counts,
    error_message,
    format_creator,
    format_license,
    format_similarity,
    resolve_media_url,
    safe_link_url,
)

BASE_URL = "http://localhost:8000"


def test_resolve_media_url_joins_relative_path() -> None:
    assert (
        resolve_media_url(BASE_URL, "/api/v1/images/abc")
        == "http://localhost:8000/api/v1/images/abc"
    )
    assert (
        resolve_media_url(BASE_URL + "/", "api/v1/images/abc")
        == "http://localhost:8000/api/v1/images/abc"
    )


def test_resolve_media_url_keeps_same_origin_absolute_url() -> None:
    url = "http://localhost:8000/api/v1/images/abc"
    assert resolve_media_url(BASE_URL, url) == url


def test_resolve_media_url_rejects_other_hosts() -> None:
    # The frontend must not fetch image bytes directly from MinIO/Openverse.
    assert (
        resolve_media_url(BASE_URL, "http://minio:9000/images/bank-mas/a.jpg") is None
    )
    assert resolve_media_url(BASE_URL, "https://cdn.example.com/image.jpg") is None


def test_resolve_media_url_resolves_against_public_base_path() -> None:
    assert (
        resolve_media_url("http://localhost:8000/gateway", "/api/v1/images/abc")
        == "http://localhost:8000/api/v1/images/abc"
    )


def test_resolve_media_url_uses_only_the_public_api_host() -> None:
    public_base_url = "http://127.0.0.1:8000"

    # Relative imageUrl from the backend resolves to the browser-facing host.
    assert (
        resolve_media_url(public_base_url, "/api/v1/images/abc")
        == "http://127.0.0.1:8000/api/v1/images/abc"
    )
    # The internal server-side host is not the public host, so it is rejected.
    assert (
        resolve_media_url(public_base_url, "http://backend:8000/api/v1/images/abc")
        is None
    )
    assert (
        resolve_media_url(public_base_url, "http://127.0.0.1:8000/api/v1/images/abc")
        == "http://127.0.0.1:8000/api/v1/images/abc"
    )


def test_resolve_media_url_rejects_unsafe_or_empty_values() -> None:
    assert resolve_media_url(BASE_URL, None) is None
    assert resolve_media_url(BASE_URL, "   ") is None
    assert resolve_media_url(BASE_URL, "javascript:alert(1)") is None
    assert resolve_media_url(BASE_URL, "ftp://example.com/a.jpg") is None


def test_safe_link_url_allows_only_http_schemes() -> None:
    assert safe_link_url("https://example.com/a") == "https://example.com/a"
    assert safe_link_url("javascript:alert(1)") is None
    assert safe_link_url(None) is None


def test_format_similarity_handles_missing_score() -> None:
    assert format_similarity(0.81234) == "Skor kemiripan: 0.8123"
    assert "tidak tersedia" in format_similarity(None)


def test_attribution_fallbacks_are_explicit() -> None:
    assert format_creator("  An Artist  ") == "An Artist"
    assert format_creator(None) == "Pembuat tidak tercatat"
    assert format_creator("   ") == "Pembuat tidak tercatat"
    assert format_license("cc-by") == "cc-by"
    assert format_license(None) == "Lisensi tidak tercatat"


def test_available_counts_only_includes_provided_values() -> None:
    status = FetchJobStatus(
        job_id="fetch-1",
        fetch_status="COMPLETED",
        downloaded=10,
        failed_downloads=0,
        indexed=None,
        duplicates=1,
    )
    assert available_counts(status) == (
        ("Terunduh", 10),
        ("Gagal diunduh", 0),
        ("Duplikat", 1),
    )


def test_available_counts_can_be_empty() -> None:
    status = FetchJobStatus(job_id="fetch-1", fetch_status="FETCHING")
    assert available_counts(status) == ()


def test_error_messages_cover_api_failure_modes() -> None:
    timeout = error_message(ApiTimeoutError("slow"))
    assert "batas waktu" in timeout

    connection = error_message(ApiConnectionError("refused"))
    assert "Tidak dapat terhubung" in connection
    assert "API_BASE_URL" in connection

    malformed = error_message(MalformedResponseError("Respons rusak"))
    assert malformed == "Respons rusak"

    http = error_message(ApiError("detail dari API (HTTP 422)", status_code=422))
    assert "API menolak permintaan" in http
    assert "detail dari API" in http

    unexpected = error_message(RuntimeError("boom"))
    assert "kesalahan tak terduga" in unexpected
