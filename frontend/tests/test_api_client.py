"""Unit tests for the Python API client using httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from image_search.api_client import (
    ApiClient,
    ApiConnectionError,
    ApiError,
    ApiTimeoutError,
    MalformedResponseError,
)

BASE_URL = "http://api.test:8000"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> ApiClient:
    return ApiClient(
        BASE_URL,
        request_timeout=5.0,
        search_timeout=12.0,
        transport=httpx.MockTransport(handler),
    )


def test_create_fetch_job_sends_contract_payload_and_parses_response() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(202, json={"jobId": "fetch-1", "status": "FETCHING"})

    with make_client(handler) as client:
        created = client.create_fetch_job(["dog", "cat"], 5)

    assert created.job_id == "fetch-1"
    assert created.status == "FETCHING"

    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/api/v1/fetch-jobs"
    assert json.loads(request.content) == {
        "classes": ["dog", "cat"],
        "imagesPerClass": 5,
    }
    assert request.headers["content-type"] == "application/json"


def test_get_fetch_job_parses_combined_status_and_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/fetch-jobs/fetch-1"
        return httpx.Response(
            200,
            json={
                "jobId": "fetch-1",
                "fetchStatus": "COMPLETED",
                "ingestionStatus": "INDEXED",
                "downloaded": 55,
                "failedDownloads": 5,
                "indexed": 54,
                "duplicates": 1,
                "errors": ["image abc: decode failed"],
            },
        )

    with make_client(handler) as client:
        status = client.get_fetch_job("fetch-1")

    assert status.job_id == "fetch-1"
    assert status.fetch_status == "COMPLETED"
    assert status.ingestion_status == "INDEXED"
    assert status.downloaded == 55
    assert status.failed_downloads == 5
    assert status.indexed == 54
    assert status.duplicates == 1
    assert status.errors == ("image abc: decode failed",)


def test_get_fetch_job_accepts_missing_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobId": "fetch-2", "fetchStatus": "FETCHING"})

    with make_client(handler) as client:
        status = client.get_fetch_job("fetch-2")

    assert status.ingestion_status is None
    assert status.downloaded is None
    assert status.failed_downloads is None
    assert status.indexed is None
    assert status.duplicates is None
    assert status.errors == ()


def test_failed_downloads_list_is_counted_not_rendered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobId": "fetch-3",
                "fetchStatus": "PARTIAL",
                "failedDownloads": [{"url": "https://x/1"}, {"url": "https://x/2"}],
            },
        )

    with make_client(handler) as client:
        status = client.get_fetch_job("fetch-3")

    assert status.failed_downloads == 2


def test_job_id_is_url_encoded() -> None:
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.raw_path
        return httpx.Response(200, json={"jobId": "a/b c", "fetchStatus": "FETCHING"})

    with make_client(handler) as client:
        client.get_fetch_job("a/b c")

    assert b"/api/v1/fetch-jobs/a%2Fb%20c" in captured["path"]


def test_retry_ingestion_accepts_empty_202_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/ingest/fetch-1"
        return httpx.Response(202)

    with make_client(handler) as client:
        result = client.retry_ingestion("fetch-1")

    assert result.job_id == "fetch-1"
    assert result.status is None


def test_retry_ingestion_parses_optional_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"jobId": "fetch-1", "status": "PENDING"})

    with make_client(handler) as client:
        result = client.retry_ingestion("fetch-1")

    assert result.status == "PENDING"


def test_search_sends_query_and_parses_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "query": "a dog playing in the snow",
                "results": [
                    {
                        "imageId": "abc",
                        "score": 0.8123,
                        "imageUrl": "/api/v1/images/abc",
                        "contentType": "image/jpeg",
                        "sourceUrl": "https://commons.wikimedia.org/wiki/File:Dog.jpg",
                        "license": "cc-by",
                        "creator": "Someone",
                    },
                    {"imageId": "def", "imageUrl": "/api/v1/images/def"},
                ],
            },
        )

    with make_client(handler) as client:
        response = client.search("a dog playing in the snow", 12)

    assert captured["payload"] == {
        "query": "a dog playing in the snow",
        "topK": 12,
    }
    assert response.query == "a dog playing in the snow"
    assert len(response.results) == 2
    first = response.results[0]
    assert first.image_id == "abc"
    assert first.score == pytest.approx(0.8123)
    assert first.image_url == "/api/v1/images/abc"
    assert first.creator == "Someone"
    assert first.license == "cc-by"
    assert first.source_url == "https://commons.wikimedia.org/wiki/File:Dog.jpg"
    assert first.content_type == "image/jpeg"
    assert response.results[1].score is None


def test_search_rejects_result_without_image_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"query": "dog", "results": [{"imageId": "abc"}]},
        )

    with (
        make_client(handler) as client,
        pytest.raises(MalformedResponseError) as excinfo,
    ):
        client.search("dog", 5)

    assert "imageUrl" in str(excinfo.value)


def test_http_error_uses_fastapi_validation_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "topK"],
                        "msg": "Input should be less than or equal to 50",
                        "type": "less_than_equal",
                    }
                ]
            },
        )

    with make_client(handler) as client, pytest.raises(ApiError) as excinfo:
        client.search("dog", 500)

    error = excinfo.value
    assert error.status_code == 422
    assert "topK" in error.message
    assert "less than or equal to 50" in error.message


def test_http_error_falls_back_to_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    with make_client(handler) as client, pytest.raises(ApiError) as excinfo:
        client.get_fetch_job("fetch-1")

    assert excinfo.value.status_code == 503
    assert "HTTP 503" in excinfo.value.message


def test_timeout_raises_specific_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with make_client(handler) as client, pytest.raises(ApiTimeoutError):
        client.get_fetch_job("fetch-1")


def test_connection_failure_raises_specific_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with make_client(handler) as client, pytest.raises(ApiConnectionError) as excinfo:
        client.get_fetch_job("fetch-1")

    assert BASE_URL in str(excinfo.value)


def test_invalid_json_raises_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with make_client(handler) as client, pytest.raises(MalformedResponseError):
        client.get_fetch_job("fetch-1")


def test_empty_body_on_status_raises_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    with make_client(handler) as client, pytest.raises(MalformedResponseError):
        client.get_fetch_job("fetch-1")


def test_negative_count_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jobId": "fetch-1", "fetchStatus": "COMPLETED", "indexed": -1},
        )

    with make_client(handler) as client, pytest.raises(MalformedResponseError):
        client.get_fetch_job("fetch-1")


def test_search_timeout_is_passed_per_request() -> None:
    captured: dict[str, float | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout")
        captured["timeout"] = (
            timeout.get("connect") if isinstance(timeout, dict) else None
        )
        return httpx.Response(200, json={"query": "dog", "results": []})

    with make_client(handler) as client:
        client.search("dog", 5)

    assert captured["timeout"] == pytest.approx(12.0)


def test_invalid_base_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        ApiClient("localhost:8000")

    with pytest.raises(ValueError):
        ApiClient("ftp://localhost:8000")
