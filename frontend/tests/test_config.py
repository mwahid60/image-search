"""Unit tests for environment configuration."""

from __future__ import annotations

import pytest

from image_search.config import (
    DEFAULT_API_BASE_URL,
    ConfigError,
    load_config,
    normalize_base_url,
)


def test_public_base_url_defaults_to_api_base_url() -> None:
    config = load_config({"API_BASE_URL": "http://backend:8000/"})

    assert config.api_base_url == "http://backend:8000"
    assert config.public_api_base_url == config.api_base_url


def test_public_base_url_defaults_when_environment_is_empty() -> None:
    config = load_config({})

    assert config.api_base_url == DEFAULT_API_BASE_URL
    assert config.public_api_base_url == DEFAULT_API_BASE_URL


def test_public_base_url_can_differ_from_server_base_url() -> None:
    config = load_config(
        {
            "API_BASE_URL": "http://backend:8000",
            "PUBLIC_API_BASE_URL": "http://127.0.0.1:8000/",
        }
    )

    assert config.api_base_url == "http://backend:8000"
    assert config.public_api_base_url == "http://127.0.0.1:8000"


def test_blank_public_base_url_falls_back_to_api_base_url() -> None:
    config = load_config(
        {
            "API_BASE_URL": "http://backend:8000",
            "PUBLIC_API_BASE_URL": "   ",
        }
    )

    assert config.public_api_base_url == "http://backend:8000"


def test_invalid_public_base_url_names_the_public_variable() -> None:
    with pytest.raises(ConfigError, match="PUBLIC_API_BASE_URL"):
        load_config(
            {
                "API_BASE_URL": "http://backend:8000",
                "PUBLIC_API_BASE_URL": "127.0.0.1:8000",
            }
        )


def test_invalid_api_base_url_names_the_api_variable() -> None:
    with pytest.raises(ConfigError, match="API_BASE_URL"):
        load_config({"API_BASE_URL": "ftp://backend:8000"})


def test_normalize_base_url_rejects_query_and_fragment() -> None:
    with pytest.raises(ConfigError):
        normalize_base_url("http://localhost:8000?token=secret")

    with pytest.raises(ConfigError):
        normalize_base_url("http://localhost:8000#section")


def test_config_keeps_timeout_and_polling_validation() -> None:
    with pytest.raises(ConfigError):
        load_config({"API_TIMEOUT_SECONDS": "not-a-number"})

    with pytest.raises(ConfigError):
        load_config({"FRONTEND_MAX_AUTO_POLLS": "0"})
