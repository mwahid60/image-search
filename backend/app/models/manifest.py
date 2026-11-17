"""Pydantic models mirroring the fetch manifest contract written by Java."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

ManifestStatus = Literal["COMPLETED", "PARTIAL", "FAILED"]


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


class FailedDownload(CamelModel):
    requested_class: str | None = None
    url: str | None = None
    reason: str | None = None


class ManifestImage(CamelModel):
    image_id: str
    object_key: str
    content_type: str | None = None
    sha256: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    source_url: str | None = None
    license: str | None = None
    creator: str | None = None
    # Kept as a raw JSON object so ingestion copies it verbatim into Milvus.
    evaluation_metadata: dict[str, Any] = Field(default_factory=dict)


class FetchManifest(CamelModel):
    job_id: str
    status: ManifestStatus | str
    requested_classes: list[str] = Field(default_factory=list)
    images: list[ManifestImage] = Field(default_factory=list)
    failed_downloads: list[FailedDownload] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def is_ingestable(self) -> bool:
        return self.status in {"COMPLETED", "PARTIAL"} and bool(self.images)
