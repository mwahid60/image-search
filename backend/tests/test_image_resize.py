"""Embedding-image downscale helper tests (no large memory allocations)."""

from __future__ import annotations

import io

from app.ingestion.images import prepare_embedding_image
from PIL import Image
from tests.support import png_bytes


def png_rgba(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGBA", size, (255, 0, 0, 0))
    image.putpixel((size[0] // 2, size[1] // 2), (0, 255, 0, 255))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_with_orientation(size: tuple[int, int], orientation: int) -> bytes:
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", size, "blue").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_small_image_is_returned_unchanged() -> None:
    data = png_bytes("red", size=(16, 12))
    output, mime = prepare_embedding_image(data, "image/png", max_dimension=1024)
    assert output == data
    assert mime == "image/png"


def test_large_image_is_downscaled_and_reencoded() -> None:
    data = png_bytes("green", size=(2000, 1500))
    output, mime = prepare_embedding_image(data, "image/png", max_dimension=1024)

    assert output != data
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(output)) as image:
        assert max(image.size) == 1024
        assert image.size == (1024, 768)


def test_alpha_is_preserved_when_downscaling() -> None:
    data = png_rgba((1600, 1200))
    output, mime = prepare_embedding_image(data, "image/png", max_dimension=512)

    assert mime == "image/png"
    with Image.open(io.BytesIO(output)) as image:
        assert image.mode == "RGBA"
        assert max(image.size) == 512
        assert image.getpixel((0, 0))[3] == 0  # transparency retained


def test_exif_orientation_is_applied_before_downscaling() -> None:
    data = jpeg_with_orientation((1200, 800), orientation=6)
    output, mime = prepare_embedding_image(data, "image/jpeg", max_dimension=1024)

    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(output)) as image:
        width, height = image.size
        assert max(width, height) <= 1024
        assert height > width  # orientation 6 rotates to portrait
