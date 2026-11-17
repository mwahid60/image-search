"""Image validation helpers (decode + MIME detection) used by ingestion."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ImageValidationError(Exception):
    """Raised when MinIO bytes are not a decodable supported image."""


@dataclass(frozen=True, slots=True)
class ImageInfo:
    mime_type: str
    width: int
    height: int
    format: str


def inspect_image(data: bytes) -> ImageInfo:
    if not data:
        raise ImageValidationError("image bytes are empty")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = (image.format or "").upper()
            width, height = image.size
            mime = Image.MIME.get(fmt, "")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("object cannot be decoded as an image") from exc
    if mime not in ALLOWED_IMAGE_MIME:
        raise ImageValidationError(f"unsupported image format: {fmt or 'unknown'}")
    if width <= 0 or height <= 0:
        raise ImageValidationError("image has invalid dimensions")
    return ImageInfo(mime_type=mime, width=width, height=height, format=fmt)
