"""Formatting helpers shared by the Streamlit pages."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from .api_client import (
    ApiConnectionError,
    ApiError,
    ApiTimeoutError,
    FetchJobStatus,
    MalformedResponseError,
)

_COUNTS: tuple[tuple[str, str], ...] = (
    ("downloaded", "Terunduh"),
    ("failed_downloads", "Gagal diunduh"),
    ("indexed", "Ter-index"),
    ("duplicates", "Duplikat"),
)


def resolve_media_url(base_url: str, image_url: str | None) -> str | None:
    """Turn a relative ``imageUrl`` into an absolute HTTP(S) URL.

    Returns ``None`` when the URL uses a scheme that should not be rendered
    (for example ``javascript:``) or is empty.
    """

    if not image_url or not image_url.strip():
        return None

    value = image_url.strip()
    parts = urlsplit(value)
    if parts.scheme:
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return None
        base_parts = urlsplit(base_url)
        # Only the Python API itself may serve image bytes; reject absolute
        # URLs that point at MinIO, Openverse, or any other host.
        if (parts.scheme.lower(), parts.netloc.lower()) != (
            base_parts.scheme.lower(),
            base_parts.netloc.lower(),
        ):
            return None
        return value

    base = base_url.strip().rstrip("/")
    if not base:
        return None
    return urljoin(f"{base}/", value)


def safe_link_url(url: str | None) -> str | None:
    """Return the URL only when it is safe to use as an HTTP(S) link."""

    if not url or not url.strip():
        return None
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return value
    return None


def format_similarity(score: float | None) -> str:
    if score is None:
        return "Skor kemiripan tidak tersedia"
    return f"Skor kemiripan: {score:.4f}"


def format_creator(creator: str | None) -> str:
    return creator.strip() if creator and creator.strip() else "Pembuat tidak tercatat"


def format_license(license_name: str | None) -> str:
    if license_name and license_name.strip():
        return license_name.strip()
    return "Lisensi tidak tercatat"


def available_counts(status: FetchJobStatus) -> tuple[tuple[str, int], ...]:
    """Return only the counts the backend actually provided."""

    values: list[tuple[str, int]] = []
    for attribute, label in _COUNTS:
        value = getattr(status, attribute)
        if value is not None:
            values.append((label, value))
    return tuple(values)


def error_message(exc: Exception) -> str:
    """Translate an exception into a short, actionable UI message."""

    if isinstance(exc, ApiTimeoutError):
        return (
            "Python API tidak merespons dalam batas waktu. Coba lagi, atau "
            "naikkan API_TIMEOUT_SECONDS / API_SEARCH_TIMEOUT_SECONDS."
        )
    if isinstance(exc, ApiConnectionError):
        return (
            "Tidak dapat terhubung ke Python API. Pastikan backend berjalan "
            "dan API_BASE_URL sudah benar."
        )
    if isinstance(exc, MalformedResponseError):
        return exc.message
    if isinstance(exc, ApiError):
        if exc.status_code is not None:
            return f"API menolak permintaan: {exc.message}"
        return exc.message
    return f"Terjadi kesalahan tak terduga: {exc}"
