"""SSE wire-format parsing and consumer behaviour."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

from app.integrations.sse import FetchCompletedEvent, FetchEventConsumer, SSEFrameParser
from tests.support import make_settings


def completed_lines(
    event_id: str,
    job_id: str,
    event: str = "fetch.completed",
    manifest_key: str | None = None,
) -> list[str]:
    key = manifest_key or f"jobs/bank-mas/{job_id}/fetch-manifest.json"
    payload = json.dumps({"jobId": job_id, "manifestKey": key})
    return [f"id: {event_id}", f"event: {event}", f"data: {payload}", ""]


def test_parser_parses_event_fields_and_resets() -> None:
    parser = SSEFrameParser()
    for line in [
        "id: fetch:fetch-1:completed",
        "event: fetch.completed",
        'data: {"jobId":"fetch-1",',
        'data: "manifestKey":"jobs/bank-mas/fetch-1/fetch-manifest.json"}',
    ]:
        assert parser.feed_line(line) is None
    event = parser.feed_line("")
    assert event is not None
    assert event.event_id == "fetch:fetch-1:completed"
    assert event.event == "fetch.completed"
    assert event.data.endswith('"}')
    assert '"jobId":"fetch-1"' in event.data

    # Event fields must not leak into the next frame.
    parser.feed_line("id: next")
    parser.feed_line("data: {}")
    event = parser.feed_line("")
    assert event is not None
    assert event.event_id == "next"
    assert event.event == "message"
    assert event.data == "{}"


def test_parser_dispatch_of_multiline_data() -> None:
    parser = SSEFrameParser()
    for line in ["id: 1", "event: fetch.completed", "data: a", "data: b"]:
        assert parser.feed_line(line) is None
    event = parser.feed_line("")
    assert event is not None
    assert event.data == "a\nb"
    assert event.event_id == "1"


def test_parser_ignores_comments_and_heartbeats() -> None:
    parser = SSEFrameParser()
    assert parser.feed_line(": heartbeat") is None
    assert parser.feed_line("") is None
    for line in ["event: fetch.completed", "data: {}", ""]:
        event = parser.feed_line(line)
    assert event is not None


def test_parser_handles_crlf_and_retry_field() -> None:
    parser = SSEFrameParser()
    for line in ["id: 7\r", "event: fetch.completed\r", "data: {}\r", "retry: 1500\r", "\r"]:
        event = parser.feed_line(line)
    assert event is not None
    assert event.event_id == "7"
    assert event.retry == 1500
    assert event.data == "{}"


class ScriptedConsumer(FetchEventConsumer):
    """Consumer with scripted SSE line streams instead of a real Java server."""

    def __init__(self, *args, scripts: Sequence[Sequence[str]], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._scripts = list(scripts)
        self.stream_attempts = 0

    async def _stream_lines(self) -> AsyncIterator[str]:
        self.stream_attempts += 1
        if self._scripts:
            script = self._scripts.pop(0)
        else:
            await asyncio.sleep(0.005)
            raise ConnectionResetError("scripted disconnect")
        for line in script:
            yield line
        raise ConnectionResetError("stream closed after script")


async def test_consumer_dispatches_events_and_reconnects() -> None:
    job_one, job_two = "fetch-one", "fetch-two"
    scripts = [
        [": heartbeat", *completed_lines("evt-1", job_one)],
        [
            *completed_lines("evt-1", job_one),  # duplicate event id
            *completed_lines("evt-2", job_two),
            "event: fetch.started",
            'data: {"jobId":"fetch-x"}',
            "",
        ],
    ]
    received: list[FetchCompletedEvent] = []
    done = asyncio.Event()

    async def handler(event: FetchCompletedEvent) -> None:
        received.append(event)
        if len(received) >= 2:
            done.set()

    consumer = ScriptedConsumer(make_settings(), handler, scripts=scripts)
    await consumer.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=3)
    finally:
        await consumer.stop()

    assert [event.job_id for event in received] == [job_one, job_two]
    assert received[0].manifest_key == f"jobs/bank-mas/{job_one}/fetch-manifest.json"
    snapshot = consumer.snapshot()
    assert snapshot["eventsReceived"] == 2
    assert snapshot["duplicatesIgnored"] == 1
    assert snapshot["reconnects"] >= 1
    assert snapshot["running"] is False


async def test_consumer_rejects_malformed_and_mismatched_events() -> None:
    scripts = [
        [
            "id: bad-json",
            "event: fetch.completed",
            "data: not-json",
            "",
            "id: wrong-key",
            "event: fetch.completed",
            'data: {"jobId":"fetch-1","manifestKey":"jobs/bank-mas/other/fetch-manifest.json"}',
            "",
            "id: wrong-shape",
            "event: fetch.completed",
            'data: {"manifestKey":"jobs/bank-mas/fetch-1/fetch-manifest.json"}',
            "",
        ]
    ]
    received: list[FetchCompletedEvent] = []

    async def handler(event: FetchCompletedEvent) -> None:
        received.append(event)

    consumer = ScriptedConsumer(make_settings(), handler, scripts=scripts)
    await consumer.start()
    await asyncio.sleep(0.15)
    await consumer.stop()

    assert received == []
    assert consumer.snapshot()["invalidEvents"] == 3


async def test_consumer_survives_handler_errors() -> None:
    scripts = [
        [*completed_lines("evt-1", "fetch-1")],
        [*completed_lines("evt-2", "fetch-2")],
    ]
    processed: list[str] = []
    done = asyncio.Event()

    async def handler(event: FetchCompletedEvent) -> None:
        if event.job_id == "fetch-1":
            raise RuntimeError("boom")
        processed.append(event.job_id)
        done.set()

    consumer = ScriptedConsumer(make_settings(), handler, scripts=scripts)
    await consumer.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=3)
    finally:
        await consumer.stop()

    assert processed == ["fetch-2"]
    assert consumer.snapshot()["handlerErrors"] == 1


async def test_consumer_does_not_crash_when_stream_always_fails() -> None:
    async def handler(_: FetchCompletedEvent) -> None:
        raise AssertionError("should not be called")

    consumer = ScriptedConsumer(make_settings(), handler, scripts=[])
    await consumer.start()
    await asyncio.sleep(0.1)
    snapshot = consumer.snapshot()
    await consumer.stop()
    assert snapshot["connected"] is False
    assert snapshot["reconnects"] >= 1
    assert snapshot["lastError"]
    assert consumer.snapshot()["running"] is False
