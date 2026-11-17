"""Internal data structures for vector records and search results.

`ImageSearchHit` deliberately has no `evaluation_metadata` field so evaluation
data can never leak into a search response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ImageVectorRecord:
    image_id: str
    embedding: list[float]
    object_key: str
    content_type: str
    width: int
    height: int
    sha256: str
    source_url: str
    license: str
    creator: str
    evaluation_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0


@dataclass(frozen=True, slots=True)
class ImageSearchHit:
    image_id: str
    score: float
    object_key: str
    content_type: str
    width: int = 0
    height: int = 0
    sha256: str = ""
    source_url: str = ""
    license: str = ""
    creator: str = ""


@dataclass(frozen=True, slots=True)
class ImageReference:
    image_id: str
    object_key: str
    content_type: str
    sha256: str = ""
