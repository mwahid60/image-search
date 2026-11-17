"""MinIO connection-pool configuration tests."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.integrations.object_store import MinioObjectStore
from tests.support import make_settings


def test_settings_reads_minio_pool_maxsize_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_POOL_MAXSIZE", "64")
    settings = Settings(_env_file=None)
    assert settings.minio_pool_maxsize == 64


def test_minio_pool_maxsize_default_is_32() -> None:
    assert Settings(_env_file=None).minio_pool_maxsize == 32


def test_object_store_builds_pool_with_configured_size(monkeypatch) -> None:
    settings = make_settings(minio_pool_maxsize=48)
    captured: dict[str, Any] = {}

    class FakeMinio:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

    import app.integrations.object_store as object_store_module

    monkeypatch.setattr(object_store_module, "Minio", FakeMinio)
    store = MinioObjectStore(settings)

    pool = captured["http_client"]
    assert pool.connection_pool_kw["maxsize"] == 48
    assert pool.connection_pool_kw["block"] is True
    assert store._bucket == settings.minio_bucket
