"""Milvus configuration tests: Lite db path env and client wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.integrations.vector_store import MilvusVectorStore
from tests.support import make_settings


def test_settings_reads_milvus_db_path_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MILVUS_DB_PATH", "data/from-env-milvus.db")
    monkeypatch.delenv("MILVUS_URI", raising=False)

    settings = Settings(_env_file=None)

    assert settings.milvus_db_path == "data/from-env-milvus.db"


def test_milvus_uri_env_is_not_used_as_lite_db_path(monkeypatch) -> None:
    monkeypatch.delenv("MILVUS_DB_PATH", raising=False)
    monkeypatch.setenv("MILVUS_URI", "http://localhost:19530")

    settings = Settings(_env_file=None)

    assert settings.milvus_db_path == "data/milvus.db"


def test_milvus_db_path_must_not_be_blank(monkeypatch) -> None:
    monkeypatch.setenv("MILVUS_DB_PATH", "   ")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_vector_store_passes_db_path_to_milvus_client(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "custom-milvus.db"
    settings = make_settings(milvus_db_path=str(db_path), milvus_token=None)
    captured: dict[str, object] = {}

    class FakeMilvusClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    import pymilvus

    monkeypatch.setattr(pymilvus, "MilvusClient", FakeMilvusClient)
    store = MilvusVectorStore(settings)

    assert isinstance(store.client, FakeMilvusClient)
    assert captured == {"uri": str(db_path)}


def test_vector_store_passes_token_only_when_configured(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "token-milvus.db"
    settings = make_settings(milvus_db_path=str(db_path), milvus_token="secret-token")
    captured: dict[str, object] = {}

    class FakeMilvusClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    import pymilvus

    monkeypatch.setattr(pymilvus, "MilvusClient", FakeMilvusClient)
    store = MilvusVectorStore(settings)
    created = store.client

    assert isinstance(created, FakeMilvusClient)
    assert captured["uri"] == str(db_path)
    assert captured["token"] == "secret-token"


def test_vector_store_omits_token_for_lite(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(milvus_db_path=str(tmp_path / "lite.db"), milvus_token=None)
    captured: dict[str, object] = {}

    class FakeMilvusClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    import pymilvus

    monkeypatch.setattr(pymilvus, "MilvusClient", FakeMilvusClient)
    store = MilvusVectorStore(settings)
    created = store.client

    assert isinstance(created, FakeMilvusClient)
    assert "token" not in captured
