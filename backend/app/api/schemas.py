"""Request/response schemas for the public API."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from app.models.manifest import CamelModel

_CLASS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&.\-]{0,63}$")


class FetchJobRequest(CamelModel):
    classes: list[str] = Field(min_length=1)
    images_per_class: int = Field(ge=1, le=100)

    @field_validator("classes")
    @classmethod
    def _validate_classes(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            name = (raw or "").strip()
            if not name:
                raise ValueError("class names must not be empty")
            if not _CLASS_NAME.match(name):
                raise ValueError(f"invalid class name: {name!r}")
            if name.lower() in {existing.lower() for existing in cleaned}:
                continue
            cleaned.append(name)
        if not cleaned:
            raise ValueError("at least one class is required")
        return cleaned


class FetchJobAcceptedResponse(CamelModel):
    job_id: str
    status: str


class IngestAcceptedResponse(CamelModel):
    job_id: str
    status: str
    reason: str | None = None


class SearchRequest(CamelModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=50)


class ErrorDetail(CamelModel):
    code: str
    message: str
    details: object | None = None
