"""Application configuration loaded from environment variables.

All operational values (Java base URL, MinIO, llama.cpp, Milvus, prefixes) are
configurable. No credential defaults are baked in.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_IMAGE_PREFIX = "images/bank-mas/"
DEFAULT_JOBS_PREFIX = "jobs/bank-mas/"
FETCH_MANIFEST_FILENAME = "fetch-manifest.json"
INGESTION_STATUS_FILENAME = "ingestion-status.json"


class Settings(BaseSettings):
    """Runtime settings. Environment variable names are the field names (upper-case)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "bank-mas-image-search-backend"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    # --- Java fetcher (upstream fetch orchestration + SSE server) ---
    java_base_url: str = "http://localhost:8081"
    java_http_timeout_seconds: float = Field(default=10.0, gt=0)
    java_events_path: str = "/api/v1/events/fetch"

    # --- SSE consumer ---
    sse_enabled: bool = True
    sse_reconnect_initial_seconds: float = Field(default=1.0, gt=0)
    sse_reconnect_max_seconds: float = Field(default=30.0, gt=0)
    sse_read_timeout_seconds: float = Field(default=120.0, gt=0)
    sse_connect_timeout_seconds: float = Field(default=10.0, gt=0)

    # --- MinIO / S3 ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_secure: bool = False
    minio_region: str | None = None
    minio_bucket: str = "bank-mas"
    minio_ensure_bucket: bool = True
    minio_timeout_seconds: float = Field(default=5.0, gt=0)
    minio_max_retries: int = Field(default=2, ge=0, le=10)
    image_prefix: str = DEFAULT_IMAGE_PREFIX
    jobs_prefix: str = DEFAULT_JOBS_PREFIX
    fetch_manifest_filename: str = FETCH_MANIFEST_FILENAME
    ingestion_status_filename: str = INGESTION_STATUS_FILENAME

    # --- llama.cpp embeddings ---
    llama_base_url: str = "http://localhost:8080"
    llama_model: str = "embeddinggemma-2"
    llama_timeout_seconds: float = Field(default=120.0, gt=0)
    embedding_dimension: int = Field(default=768, gt=0)
    # Task prefix from the EmbeddingGemma model card. Must stay consistent between
    # ingestion (images only) and query embedding.
    query_prompt_prefix: str = "task: search result | query: "

    # --- Milvus ---
    milvus_uri: str = "data/milvus.db"
    milvus_token: str | None = None
    milvus_collection: str = "image_embeddings"
    milvus_metric_type: str = "COSINE"
    milvus_index_type: str = "FLAT"

    # --- Search ---
    search_default_top_k: int = Field(default=10, ge=1)
    search_max_top_k: int = Field(default=50, ge=1)
    search_max_query_length: int = Field(default=500, ge=1)

    # --- Ingestion ---
    ingest_batch_size: int = Field(default=4, ge=1, le=16)
    ingest_max_image_bytes: int = Field(default=15 * 1024 * 1024, gt=0)

    # --- Reconciliation ---
    reconciliation_enabled: bool = True
    reconciliation_on_startup: bool = True
    reconciliation_interval_seconds: float = Field(default=300.0, gt=0)
    reconciliation_retry_failed: bool = False

    # --- Fetch proxy validation limits ---
    max_classes_per_request: int = Field(default=50, ge=1)
    max_images_per_class: int = Field(default=50, ge=1)

    # --- Test / embedding-into-host switches ---
    background_tasks_enabled: bool = True

    @field_validator("image_prefix", "jobs_prefix")
    @classmethod
    def _normalize_prefix(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prefix must not be empty")
        return value if value.endswith("/") else f"{value}/"

    @field_validator("java_base_url", "llama_base_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("java_events_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            value = f"/{value}"
        return value

    @field_validator(
        "minio_access_key",
        "minio_secret_key",
        "minio_region",
        "milvus_token",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @property
    def java_events_url(self) -> str:
        return f"{self.java_base_url}{self.java_events_path}"

    def manifest_key(self, job_id: str) -> str:
        return f"{self.jobs_prefix}{job_id}/{self.fetch_manifest_filename}"

    def ingestion_status_key(self, job_id: str) -> str:
        return f"{self.jobs_prefix}{job_id}/{self.ingestion_status_filename}"

    def is_image_key(self, object_key: str) -> bool:
        return (
            object_key.startswith(self.image_prefix)
            and len(object_key) > len(self.image_prefix)
            and ".." not in object_key
        )

    def manifest_scan_prefix(self) -> str:
        return self.jobs_prefix

    @staticmethod
    def host_of(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path


@lru_cache
def get_settings() -> Settings:
    return Settings()
