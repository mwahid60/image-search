"""Contract tests against the actual Java `FetchJobView` payload."""

from __future__ import annotations

import httpx
from app.config import Settings
from app.integrations.java import JavaFetcherClient, JavaFetchJob
from tests.support import make_settings

JAVA_FETCH_JOB_PAYLOAD = {
    "jobId": "fetch-20261116-001",
    "fetchStatus": "FETCHING",
    "requestedClasses": ["dog"],
    "imagesPerClass": 2,
    "downloaded": 0,
    "failedDownloads": 0,
    "duplicates": 0,
    "manifestKey": None,
    "startedAt": "2026-11-16T11:00:00Z",
    "completedAt": None,
    "error": None,
}


async def test_java_client_parses_fetch_status_from_java_payload() -> None:
    settings: Settings = make_settings()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=JAVA_FETCH_JOB_PAYLOAD)
    )
    http = httpx.AsyncClient(transport=transport, base_url="http://java.test")
    client = JavaFetcherClient(settings, http)
    try:
        job = await client.get_fetch_job("fetch-20261116-001")
    finally:
        await http.aclose()

    assert job is not None
    assert job.job_id == "fetch-20261116-001"
    assert job.fetch_status == "FETCHING"
    assert job.requested_classes == ["dog"]
    assert job.images_per_class == 2
    assert job.downloaded == 0
    assert job.failed_downloads == 0
    assert job.duplicates == 0
    assert job.manifest_key is None
    assert job.started_at == "2026-11-16T11:00:00Z"
    assert job.completed_at is None
    assert job.error is None


def test_java_payload_model_uses_fetch_status_alias() -> None:
    job = JavaFetchJob.model_validate(JAVA_FETCH_JOB_PAYLOAD)
    assert job.fetch_status == "FETCHING"
    assert job.model_dump(by_alias=True)["fetchStatus"] == "FETCHING"
    assert "status" not in job.model_dump(by_alias=True)
