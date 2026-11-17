"""Streamlit-aware runtime wiring (config, cached API client)."""

from __future__ import annotations

import streamlit as st

from .api_client import ApiClient
from .config import AppConfig, ConfigError, load_config


@st.cache_resource(show_spinner=False)
def _cached_client(
    api_base_url: str, request_timeout: float, search_timeout: float
) -> ApiClient:
    return ApiClient(
        api_base_url,
        request_timeout=request_timeout,
        search_timeout=search_timeout,
    )


def get_config() -> AppConfig:
    """Load configuration or stop the page with a readable error."""

    try:
        return load_config()
    except ConfigError as exc:
        st.error(f"Konfigurasi frontend tidak valid: {exc}")
        st.stop()
        raise  # pragma: no cover - st.stop always raises


def get_client(config: AppConfig) -> ApiClient:
    return _cached_client(
        config.api_base_url,
        config.request_timeout_seconds,
        config.search_timeout_seconds,
    )
