"""Pixel-limit tests for ingestion image validation (no large allocations)."""

from __future__ import annotations

import struct
import warnings
import zlib

import pytest
from app.config import Settings
from app.ingestion.images import (
    DEFAULT_MAX_IMAGE_PIXELS,
    ImageValidationError,
    inspect_image,
)
from PIL import Image
from tests.support import png_bytes


def craft_png(width: int, height: int) -> bytes:
    """Build a PNG whose IHDR declares huge dimensions with a tiny IDAT.

    The header is valid (CRC-correct) so PIL can parse the dimensions, but the
    pixel data is only 1x1. Validation must reject it before decoding pixels.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_pixel = b"\x00" + b"\x00\x00\x00"  # filter byte + one black RGB pixel
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw_pixel))
        + chunk(b"IEND", b"")
    )


def test_normal_image_passes_validation() -> None:
    data = png_bytes("red", size=(16, 12))
    info = inspect_image(data)
    assert (info.width, info.height) == (16, 12)
    assert info.mime_type == "image/png"
    assert info.format == "PNG"


def test_declared_pixels_above_limit_are_rejected_before_decode() -> None:
    data = craft_png(6_000, 5_000)  # 30M pixels > 25M, below PIL's bomb warning
    with pytest.raises(ImageValidationError, match="pixel count 30000000"):
        inspect_image(data)


def test_pixel_limit_is_configurable() -> None:
    data = png_bytes("green", size=(16, 12))  # 192 pixels
    with pytest.raises(ImageValidationError, match="pixel count 192"):
        inspect_image(data, max_pixels=100)
    info = inspect_image(data, max_pixels=1_000)
    assert (info.width, info.height) == (16, 12)


def test_pil_decompression_bomb_warning_becomes_validation_failure() -> None:
    pil_limit = Image.MAX_IMAGE_PIXELS or DEFAULT_MAX_IMAGE_PIXELS
    side = int(pil_limit**0.5) + 1  # just above PIL's warning threshold, below 2x
    data = craft_png(side, side)
    with pytest.raises(ImageValidationError, match="pixel safety limit"):
        inspect_image(data)


def test_pil_decompression_bomb_error_becomes_validation_failure() -> None:
    pil_limit = Image.MAX_IMAGE_PIXELS or DEFAULT_MAX_IMAGE_PIXELS
    side = int((2 * pil_limit) ** 0.5) + 1
    data = craft_png(side, side)
    with pytest.raises(ImageValidationError, match="pixel safety limit"):
        inspect_image(data)


def test_validation_does_not_mutate_pil_global_state() -> None:
    data = craft_png(6_000, 5_000)
    before_max = Image.MAX_IMAGE_PIXELS
    before_filters = list(warnings.filters)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ImageValidationError):
            inspect_image(data)

    assert caught == []
    assert Image.MAX_IMAGE_PIXELS == before_max
    assert warnings.filters == before_filters


def test_settings_reads_ingest_max_image_pixels_from_env(monkeypatch) -> None:
    monkeypatch.setenv("INGEST_MAX_IMAGE_PIXELS", "12345")
    settings = Settings(_env_file=None)
    assert settings.ingest_max_image_pixels == 12345


def test_default_pixel_limit_matches_settings() -> None:
    settings = Settings(_env_file=None)
    assert settings.ingest_max_image_pixels == DEFAULT_MAX_IMAGE_PIXELS == 25_000_000
