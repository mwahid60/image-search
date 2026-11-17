#!/usr/bin/env python3
"""Smoke test for the llama.cpp EmbeddingGemma 2 embeddings server.

This script is a small integration check, not a benchmark. It verifies the
contract required by docs/rencana-implementasi.md section 12.4:

1. Two visually different images can be embedded through the multimodal
   embeddings endpoint using typed content parts.
2. Their vectors are different.
3. Every vector has the expected dimension (768 by default) and contains only
   finite numbers.
4. Text and image vectors live in the same vector space, so a text query can be
   compared with them using cosine similarity.

The relevance check (text query ranks image A above image B) is informational
with synthetic fixtures, because generated colour patterns are not guaranteed
to be semantically meaningful to the model. Pass two real images through
--image-a / --image-b and add --strict-relevance to make that check part of the
exit status.

Requirements: Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from typing import Any, Sequence

DEFAULT_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080")
DEFAULT_MODEL = os.environ.get("LLAMA_MODEL", "embeddinggemma-2")
DEFAULT_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "768"))
DEFAULT_TIMEOUT = float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "180"))

MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class SmokeTestError(RuntimeError):
    """Raised when a smoke-test check fails."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test llama.cpp text and image embeddings.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"llama-server base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--embeddings-url",
        default=os.environ.get("LLAMA_EMBEDDINGS_URL", ""),
        help="Full embeddings endpoint URL; overrides --base-url",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model alias sent in the request (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=DEFAULT_DIMENSION,
        help=f"Expected embedding dimension (default: {DEFAULT_DIMENSION})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--query",
        default="a solid red square",
        help="Text query used for the cross-modal similarity check",
    )
    parser.add_argument(
        "--task-prefix",
        default=os.environ.get("QUERY_PROMPT_PREFIX", "task: search result | query:"),
        help=(
            "Task prefix prepended to the query (default: QUERY_PROMPT_PREFIX or "
            "'task: search result | query:'); pass an empty string to disable"
        ),
    )
    parser.add_argument(
        "--image-a",
        default="",
        help="Optional first image file (PNG/JPEG/WebP); default is synthetic",
    )
    parser.add_argument(
        "--image-b",
        default="",
        help="Optional second image file (PNG/JPEG/WebP); default is synthetic",
    )
    parser.add_argument(
        "--strict-relevance",
        action="store_true",
        help="Fail when the query is not closer to image A than to image B",
    )
    return parser.parse_args(argv)


def build_embeddings_url(base_url: str, explicit_url: str) -> str:
    if explicit_url:
        return explicit_url.rstrip("/")
    base = base_url.rstrip("/")
    if base.endswith("/v1/embeddings"):
        return base
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int, pixel: Any) -> bytes:
    """Create an RGB PNG without Pillow."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type: none
        for x in range(width):
            r, g, b = pixel(x, y)
            rows.extend((r, g, b))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + png_chunk(b"IEND", b"")
    )


def synthetic_images() -> tuple[bytes, bytes]:
    size = 256

    def image_a(x: int, y: int) -> tuple[int, int, int]:
        # Red field with a white circle.
        dx, dy = x - size // 2, y - size // 2
        if dx * dx + dy * dy <= (size // 4) ** 2:
            return (255, 255, 255)
        return (210, 20, 20)

    def image_b(x: int, y: int) -> tuple[int, int, int]:
        # Blue field with green diagonal stripes.
        if (x + y) % 64 < 20:
            return (20, 180, 40)
        return (20, 40, 200)

    return (
        make_png(size, size, image_a),
        make_png(size, size, image_b),
    )


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise SmokeTestError("unsupported image format (expected PNG, JPEG, or WebP)")


def read_image(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise SmokeTestError(f"cannot read image {path}: {exc}") from exc
    if not data:
        raise SmokeTestError(f"image is empty: {path}")
    if len(data) > MAX_RESPONSE_BYTES:
        raise SmokeTestError(f"image is too large (max {MAX_RESPONSE_BYTES} bytes): {path}")
    sniff_mime(data)
    return data


def data_url(data: bytes) -> str:
    mime = sniff_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("LLAMA_API_KEY", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error: Exception | None = None
    for attempt in range(1, 4):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise SmokeTestError("embeddings response is unexpectedly large")
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SmokeTestError(f"embeddings response is not JSON: {exc}") from exc
                if not isinstance(parsed, dict):
                    raise SmokeTestError("embeddings response is not a JSON object")
                return parsed
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(2000).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - best-effort error detail
                pass
            message = f"HTTP {exc.code} from {url}"
            if detail:
                message += f": {detail}"
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                last_error = SmokeTestError(message)
                time.sleep(2 ** attempt)
                continue
            raise SmokeTestError(message) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise SmokeTestError(f"cannot reach {url}: {exc}") from exc
        except OSError as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise SmokeTestError(f"connection error while calling {url}: {exc}") from exc

    raise SmokeTestError(f"request failed after retries: {last_error}")


def parse_embeddings(response: dict[str, Any]) -> list[list[float]]:
    items: Any = None
    if isinstance(response.get("data"), list):
        items = response["data"]
    elif isinstance(response.get("embeddings"), list):
        items = response["embeddings"]
    elif isinstance(response.get("embedding"), list):
        items = [response]

    if items is None:
        raise SmokeTestError(
            "response does not contain 'data', 'embeddings', or 'embedding'"
        )

    vectors: list[list[float]] = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            vector = item.get("embedding")
        else:
            vector = item
        if not isinstance(vector, list) or not vector:
            raise SmokeTestError(f"embedding entry {index} is not a numeric vector")
        try:
            vectors.append([float(value) for value in vector])
        except (TypeError, ValueError) as exc:
            raise SmokeTestError(
                f"embedding entry {index} contains a non-numeric value: {exc}"
            ) from exc
    if not vectors:
        raise SmokeTestError("response contains no embeddings")
    return vectors


def validate_vector(vector: Sequence[float], index: int, expected_dim: int) -> float:
    if len(vector) != expected_dim:
        raise SmokeTestError(
            f"embedding {index} has dimension {len(vector)}, expected {expected_dim}"
        )
    for position, value in enumerate(vector):
        if not math.isfinite(value):
            raise SmokeTestError(
                f"embedding {index} contains a non-finite value at index {position}"
            )
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        raise SmokeTestError(f"embedding {index} has zero norm")
    return norm


def cosine(first: Sequence[float], second: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second))
    norm_a = math.sqrt(sum(a * a for a in first))
    norm_b = math.sqrt(sum(b * b for b in second))
    if norm_a == 0.0 or norm_b == 0.0:
        raise SmokeTestError("cannot compute cosine similarity for a zero vector")
    return dot / (norm_a * norm_b)


def vectors_differ(first: Sequence[float], second: Sequence[float]) -> bool:
    if list(first) == list(second):
        return False
    return cosine(first, second) < 0.9999


def embed_images(url: str, model: str, images: Sequence[bytes], timeout: float) -> list[list[float]]:
    # One image per request: llama.cpp multimodal embeddings are not guaranteed
    # to accept batches of image content parts in a single input array.
    vectors: list[list[float]] = []
    for image in images:
        payload = {
            "model": model,
            "input": [
                {
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url(image)},
                        }
                    ]
                }
            ],
        }
        vectors.extend(parse_embeddings(post_json(url, payload, timeout)))
    return vectors


def embed_text(url: str, model: str, text: str, timeout: float) -> list[float]:
    payload = {
        "model": model,
        "input": [{"content": [{"type": "text", "text": text}]}],
    }
    return parse_embeddings(post_json(url, payload, timeout))[0]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    url = build_embeddings_url(args.base_url, args.embeddings_url)

    if bool(args.image_a) != bool(args.image_b):
        print("error: pass both --image-a and --image-b, or neither", file=sys.stderr)
        return 2

    if args.image_a:
        image_a = read_image(args.image_a)
        image_b = read_image(args.image_b)
        image_a_label = args.image_a
        image_b_label = args.image_b
    else:
        image_a, image_b = synthetic_images()
        image_a_label = "synthetic red image"
        image_b_label = "synthetic blue/green image"

    print("== llama.cpp EmbeddingGemma 2 smoke test ==")
    print(f"endpoint : {url}")
    print(f"model    : {args.model}")
    print(f"images   : A={image_a_label}  B={image_b_label}")
    query_text = f"{args.task_prefix} {args.query}" if args.task_prefix else args.query
    print(f"query    : {query_text!r}")
    print()

    failures: list[str] = []

    try:
        image_vectors = embed_images(url, args.model, [image_a, image_b], args.timeout)
        text_vector = embed_text(url, args.model, query_text, args.timeout)
    except SmokeTestError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    if len(image_vectors) != 2:
        print(
            f"[FAIL] image request returned {len(image_vectors)} vectors, expected 2",
            file=sys.stderr,
        )
        return 1

    vector_a, vector_b = image_vectors
    for index, vector in enumerate(
        (vector_a, vector_b, text_vector), start=1
    ):
        try:
            norm = validate_vector(vector, index, args.dimension)
            print(f"[ok] vector {index}: dimension={len(vector)} norm={norm:.6f}")
        except SmokeTestError as exc:
            failures.append(str(exc))
            print(f"[FAIL] {exc}", file=sys.stderr)

    if not failures:
        if vectors_differ(vector_a, vector_b):
            print(
                f"[ok] image vectors differ (cosine={cosine(vector_a, vector_b):.6f})"
            )
        else:
            failures.append("image A and image B produced identical/near-identical vectors")
            print(f"[FAIL] {failures[-1]}", file=sys.stderr)

        if vectors_differ(text_vector, vector_a):
            print(
                f"[ok] text and image A vectors differ "
                f"(cosine={cosine(text_vector, vector_a):.6f})"
            )
        else:
            failures.append("text and image A produced identical/near-identical vectors")
            print(f"[FAIL] {failures[-1]}", file=sys.stderr)

        similarity_a = cosine(text_vector, vector_a)
        similarity_b = cosine(text_vector, vector_b)
        print()
        print("cross-modal similarity (informational, not a quality benchmark):")
        print(f"  query vs image A : {similarity_a:.6f} ({image_a_label})")
        print(f"  query vs image B : {similarity_b:.6f} ({image_b_label})")

        if args.strict_relevance:
            if similarity_a > similarity_b:
                print("[ok] strict relevance: image A ranks above image B")
            else:
                failures.append(
                    "strict relevance failed: image A did not rank above image B"
                )
                print(f"[FAIL] {failures[-1]}", file=sys.stderr)
        else:
            print(
                "  note: relevance is not enforced with synthetic fixtures; "
                "rerun with --image-a/--image-b and --strict-relevance for a "
                "meaningful ranking check."
            )

    print()
    if failures:
        print(f"RESULT: FAILED ({len(failures)} check(s))", file=sys.stderr)
        return 1

    print("RESULT: PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
