"""Environment-driven configuration for the Streamlit frontend.

All configuration comes from environment variables so the same image can run
locally, in Podman Compose, or on a VPS without code changes.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

ENV_API_BASE_URL = "API_BASE_URL"
ENV_REQUEST_TIMEOUT = "API_TIMEOUT_SECONDS"
ENV_SEARCH_TIMEOUT = "API_SEARCH_TIMEOUT_SECONDS"
ENV_POLL_INTERVAL = "FRONTEND_POLL_INTERVAL_SECONDS"
ENV_MAX_AUTO_POLLS = "FRONTEND_MAX_AUTO_POLLS"

DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_SEARCH_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 3.0
DEFAULT_MAX_AUTO_POLLS = 150

MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 600.0
MIN_POLL_INTERVAL_SECONDS = 1.0
MAX_POLL_INTERVAL_SECONDS = 60.0
MIN_AUTO_POLLS = 1
MAX_AUTO_POLLS = 10_000


class ConfigError(ValueError):
    """Raised when the environment contains unusable frontend configuration."""


def normalize_base_url(raw: str) -> str:
    """Validate a Python API base URL and strip any trailing slash."""

    value = (raw or "").strip()
    if not value:
        raise ConfigError(f"{ENV_API_BASE_URL} tidak boleh kosong.")

    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigError(
            f"{ENV_API_BASE_URL} harus berupa URL http(s) lengkap, contoh "
            f"'{DEFAULT_API_BASE_URL}'."
        )
    if parts.query or parts.fragment:
        raise ConfigError(
            f"{ENV_API_BASE_URL} tidak boleh mengandung query string atau fragment."
        )

    return value.rstrip("/")


def _read_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} harus berupa angka.") from exc

    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ConfigError(f"{name} harus di antara {minimum:g} dan {maximum:g}.")
    return value


def _read_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} harus berupa bilangan bulat.") from exc

    if value < minimum or value > maximum:
        raise ConfigError(f"{name} harus di antara {minimum} dan {maximum}.")
    return value


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Resolved runtime configuration for the frontend."""

    api_base_url: str
    request_timeout_seconds: float
    search_timeout_seconds: float
    poll_interval_seconds: float
    max_auto_polls: int


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    """Load and validate configuration from an environment mapping."""

    source = os.environ if env is None else env
    return AppConfig(
        api_base_url=normalize_base_url(
            source.get(ENV_API_BASE_URL) or DEFAULT_API_BASE_URL
        ),
        request_timeout_seconds=_read_float(
            source,
            ENV_REQUEST_TIMEOUT,
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
            MIN_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
        ),
        search_timeout_seconds=_read_float(
            source,
            ENV_SEARCH_TIMEOUT,
            DEFAULT_SEARCH_TIMEOUT_SECONDS,
            MIN_TIMEOUT_SECONDS,
            MAX_TIMEOUT_SECONDS,
        ),
        poll_interval_seconds=_read_float(
            source,
            ENV_POLL_INTERVAL,
            DEFAULT_POLL_INTERVAL_SECONDS,
            MIN_POLL_INTERVAL_SECONDS,
            MAX_POLL_INTERVAL_SECONDS,
        ),
        max_auto_polls=_read_int(
            source,
            ENV_MAX_AUTO_POLLS,
            DEFAULT_MAX_AUTO_POLLS,
            MIN_AUTO_POLLS,
            MAX_AUTO_POLLS,
        ),
    )
