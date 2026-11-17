"""Image validation helpers (decode + MIME detection) used by ingestion."""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Keep this in sync with the Java fetcher's pixel guard (25 megapixels).
DEFAULT_MAX_IMAGE_PIXELS = 25_000_000


class ImageValidationError(Exception):
    """Raised when MinIO bytes are not a decodable supported image."""


@dataclass(frozen=True, slots=True)
class ImageInfo:
    mime_type: str
    width: int
    height: int
    format: str


def _check_pixels(width: int, height: int, max_pixels: int) -> None:
    if width <= 0 or height <= 0:
        raise ImageValidationError("image has invalid dimensions")
    pixels = width * height
    if pixels > max_pixels:
        raise ImageValidationError(
            f"image pixel count {pixels} ({width}x{height}) exceeds limit {max_pixels}"
        )


def inspect_image(data: bytes, *, max_pixels: int = DEFAULT_MAX_IMAGE_PIXELS) -> ImageInfo:
    """Validate image bytes before they reach the embedding model.

    Dimensions are checked immediately after the header is parsed, before
    `verify()`/`load()` decode pixel data. PIL decompression-bomb warnings are
    locally promoted to validation failures; `Image.MAX_IMAGE_PIXELS` is never
    modified, so no global protection is disabled.
    """
    if not data:
        raise ImageValidationError("image bytes are empty")
    if max_pixels <= 0:
        raise ValueError("max_pixels must be positive")
    try:
        # Scope the warning filter to this call so the process-wide PIL setting
        # and warning filters are left untouched.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                _check_pixels(width, height, max_pixels)
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                _check_pixels(width, height, max_pixels)
                image.load()
                fmt = (image.format or "").upper()
                mime = Image.MIME.get(fmt, "")
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError(
            f"image exceeds the pixel safety limit of {max_pixels} pixels"
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("object cannot be decoded as an image") from exc
    if mime not in ALLOWED_IMAGE_MIME:
        raise ImageValidationError(f"unsupported image format: {fmt or 'unknown'}")
    return ImageInfo(mime_type=mime, width=width, height=height, format=fmt)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info


def prepare_embedding_image(
    data: bytes,
    content_type: str,
    *,
    max_dimension: int,
) -> tuple[bytes, str]:
    """Return an embedding-sized copy of an already validated image.

    Images whose longest side fits within `max_dimension` are returned unchanged
    so normal-size files are not re-encoded. Larger images are EXIF-orientation
    corrected, downscaled with LANCZOS, and re-encoded as PNG when alpha is
    present or JPEG otherwise. Original MinIO object, hash, and metadata are
    untouched by this helper.
    """
    if max_dimension <= 0:
        raise ValueError("max_dimension must be positive")
    normalized_mime = (content_type or "").split(";")[0].strip().lower()
    try:
        with Image.open(io.BytesIO(data)) as source:
            width, height = source.size
            if width <= 0 or height <= 0:
                raise ImageValidationError("image has invalid dimensions")
            if max(width, height) <= max_dimension:
                return data, normalized_mime

            # JPEG draft mode decodes at a reduced DCT scale when possible,
            # lowering peak memory before the resized copy is produced.
            if (source.format or "").upper() == "JPEG":
                try:
                    source.draft("RGB", (max_dimension, max_dimension))
                except (OSError, ValueError):
                    pass

            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            if _has_alpha(image):
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                image.save(buffer, format="PNG", optimize=True)
                return buffer.getvalue(), "image/png"

            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(buffer, format="JPEG", quality=85, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except ImageValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("object cannot be decoded as an image") from exc
