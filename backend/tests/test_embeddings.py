"""Embedding client tests: exact typed payloads and strict response validation."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from app.errors import EmbeddingError
from app.integrations.embeddings import LlamaCppEmbeddingsClient
from tests.support import make_settings, png_bytes


def _vector(value: float = 0.1, dim: int = 768) -> list[float]:
    return [value] * dim


def make_client(
    handler, settings=None, sleep=None
) -> tuple[LlamaCppEmbeddingsClient, list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.content:
            captured.append(json.loads(request.content))
        return handler(request)

    transport = httpx.MockTransport(wrapped)
    http = httpx.AsyncClient(transport=transport, base_url="http://llama.test")
    settings = settings or make_settings()
    return LlamaCppEmbeddingsClient(settings, http, sleep=sleep), captured


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def test_image_payload_contains_only_image_bytes() -> None:
    image = png_bytes()
    client, captured = make_client(
        lambda request: httpx.Response(200, json={"data": [{"embedding": _vector()}]})
    )
    vector = await client.embed_image(image, "image/png")

    assert len(vector) == 768
    assert len(captured) == 1
    payload = captured[0]
    assert payload == {
        "model": "embeddinggemma-2",
        "input": [
            {
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(image).decode()
                        },
                    }
                ]
            }
        ],
    }
    assert "text" not in json.dumps(payload)
    for forbidden in ("title", "tags", "requestedClass", "class", "caption", "license"):
        assert forbidden not in json.dumps(payload)


async def test_text_payload_uses_configurable_query_prefix() -> None:
    settings = make_settings(query_prompt_prefix="task: search result | query: ")
    client, captured = make_client(
        lambda request: httpx.Response(200, json={"data": [{"embedding": _vector()}]}),
        settings=settings,
    )
    await client.embed_text("a dog playing in the snow")
    payload = captured[0]
    expected_text = "task: search result | query: a dog playing in the snow"
    assert payload["input"] == [{"content": [{"type": "text", "text": expected_text}]}]
    assert payload["model"] == "embeddinggemma-2"


async def test_image_payload_rejects_non_image_content_type() -> None:
    client, captured = make_client(
        lambda request: httpx.Response(200, json={"data": [{"embedding": _vector()}]})
    )
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_image(b"abc", "text/plain")
    assert excinfo.value.code == "UNSUPPORTED_IMAGE_TYPE"
    assert captured == []


async def test_dimension_mismatch_is_rejected() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(200, json={"data": [{"embedding": _vector(dim=767)}]})
    )
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "INVALID_EMBEDDING_DIMENSION"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), "0.1", None])
async def test_non_finite_or_non_numeric_values_are_rejected(bad_value: Any) -> None:
    vector = _vector()
    vector[10] = bad_value
    body = json.dumps({"data": [{"embedding": vector}]})  # emit NaN/Infinity tokens
    client, _ = make_client(
        lambda request: httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "INVALID_EMBEDDING_VALUE"


async def test_zero_vector_is_rejected() -> None:
    client, _ = make_client(
        lambda request: httpx.Response(200, json={"data": [{"embedding": [0.0] * 768}]})
    )
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "INVALID_EMBEDDING_VALUE"


@pytest.mark.parametrize(
    "body",
    [{}, {"data": []}, {"data": [{"embedding": "nope"}]}, {"embeddings": []}],
)
async def test_malformed_response_is_rejected(body: dict[str, Any]) -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "INVALID_EMBEDDING_RESPONSE"


async def test_legacy_embeddings_array_is_supported() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json={"embeddings": [_vector()]}))
    assert len(await client.embed_text("dog")) == 768


async def test_upstream_error_and_timeout() -> None:
    client, _ = make_client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "EMBEDDING_UPSTREAM_ERROR"

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, _ = make_client(timeout_handler)
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "EMBEDDING_TIMEOUT"
    assert excinfo.value.status_code == 504


async def test_ping_reports_unavailable() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json={"status": "ok"}))
    await client.ping()

    client, _ = make_client(lambda request: httpx.Response(503, json={"status": "loading"}))
    with pytest.raises(EmbeddingError) as excinfo:
        await client.ping()
    assert excinfo.value.status_code == 503


async def test_503_is_retried_with_exponential_backoff_then_succeeds() -> None:
    calls: list[int] = []
    settings = make_settings(
        embedding_max_attempts=5,
        embedding_retry_initial_seconds=0.5,
        embedding_retry_max_seconds=1.0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json={"data": [{"embedding": _vector()}]})

    sleep = SleepRecorder()
    client, _ = make_client(handler, settings=settings, sleep=sleep)

    assert len(await client.embed_text("dog")) == 768
    assert len(calls) == 3
    assert sleep.delays == [0.5, 1.0]


async def test_503_exhausts_attempts_with_clear_error() -> None:
    calls: list[int] = []
    settings = make_settings(
        embedding_max_attempts=4,
        embedding_retry_initial_seconds=0.5,
        embedding_retry_max_seconds=1.0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="out of memory")

    sleep = SleepRecorder()
    client, _ = make_client(handler, settings=settings, sleep=sleep)

    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "EMBEDDING_UNAVAILABLE"
    assert excinfo.value.status_code == 503
    assert "after 4 attempt(s)" in excinfo.value.message
    assert len(calls) == 4
    assert sleep.delays == [0.5, 1.0, 1.0]


async def test_timeout_is_retried() -> None:
    calls: list[int] = []
    settings = make_settings(embedding_max_attempts=3)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow model", request=request)
        return httpx.Response(200, json={"data": [{"embedding": _vector()}]})

    client, _ = make_client(handler, settings=settings, sleep=SleepRecorder())
    assert len(await client.embed_text("dog")) == 768
    assert len(calls) == 2


async def test_connection_error_is_retried() -> None:
    calls: list[int] = []
    settings = make_settings(embedding_max_attempts=3)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"data": [{"embedding": _vector()}]})

    client, _ = make_client(handler, settings=settings, sleep=SleepRecorder())
    assert len(await client.embed_text("dog")) == 768
    assert len(calls) == 2


@pytest.mark.parametrize("status_code", [400, 422])
async def test_client_errors_are_not_retried(status_code: int) -> None:
    calls: list[int] = []
    sleep = SleepRecorder()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status_code, text="bad request")

    client, _ = make_client(handler, sleep=sleep)
    with pytest.raises(EmbeddingError) as excinfo:
        await client.embed_text("dog")
    assert excinfo.value.code == "EMBEDDING_UPSTREAM_ERROR"
    assert len(calls) == 1
    assert sleep.delays == []


async def test_retry_logging_never_includes_image_payload(caplog) -> None:
    image = png_bytes("red", size=(64, 64))
    encoded = base64.b64encode(image).decode()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json={"data": [{"embedding": _vector()}]})

    caplog.set_level("WARNING", logger="app.integrations.embeddings")
    client, _ = make_client(handler, sleep=SleepRecorder())
    await client.embed_image(image, "image/png")

    assert "retrying" in caplog.text
    assert encoded not in caplog.text
    assert "base64," not in caplog.text
