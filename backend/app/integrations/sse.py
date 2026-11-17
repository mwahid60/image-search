"""SSE client for Java `GET /api/v1/events/fetch`.

The consumer is the primary ingestion trigger. It reconnects with exponential
backoff, survives heartbeats/comments, deduplicates events, and never lets a
handler (or Java outage) bring down the FastAPI process.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config import Settings
from app.errors import UpstreamError
from app.logging_config import get_logger
from app.utils import isoformat, utc_now, validate_job_id

logger = get_logger(__name__)


@dataclass(slots=True)
class SSEEvent:
    event_id: str | None = None
    event: str = "message"
    data: str = ""
    retry: int | None = None


class SSEFrameParser:
    """Incremental parser for the text/event-stream wire format."""

    def __init__(self) -> None:
        self._event_id: str | None = None
        self._event_type: str = "message"
        self._data_lines: list[str] = []
        self._retry: int | None = None
        self._has_content = False

    def feed_line(self, line: str) -> SSEEvent | None:
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            # Comment frame, used by Java as a heartbeat.
            return None
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "id":
            self._event_id = value or None
        elif field_name == "event":
            self._event_type = value or "message"
        elif field_name == "data":
            self._data_lines.append(value)
        elif field_name == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                self._retry = None
        self._has_content = True
        return None

    def _dispatch(self) -> SSEEvent | None:
        if not self._has_content and not self._data_lines:
            return None
        event = SSEEvent(
            event_id=self._event_id,
            event=self._event_type,
            data="\n".join(self._data_lines),
            retry=self._retry,
        )
        self._event_id = None
        self._event_type = "message"
        self._data_lines = []
        self._retry = None
        self._has_content = False
        return event


class FetchCompletedPayload(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    job_id: str
    manifest_key: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class FetchCompletedEvent:
    event_id: str
    job_id: str
    manifest_key: str


FetchCompletedHandler = Callable[[FetchCompletedEvent], Awaitable[None]]


@dataclass(slots=True)
class ConsumerState:
    running: bool = False
    connected: bool = False
    reconnects: int = 0
    events_received: int = 0
    events_dispatched: int = 0
    duplicates_ignored: int = 0
    invalid_events: int = 0
    handler_errors: int = 0
    last_event_at: str | None = None
    last_activity_at: str | None = None
    last_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class FetchEventConsumer:
    """Background SSE consumer with reconnect + dedup."""

    def __init__(
        self,
        settings: Settings,
        handler: FetchCompletedHandler,
        client: httpx.AsyncClient | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._handler = handler
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._rng = rng or random.Random()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._state = ConsumerState()
        self._seen_event_ids: deque[str] = deque(maxlen=2000)
        self._seen_event_id_set: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._state.running = True
        self._task = asyncio.create_task(self.run(), name="sse-consumer")

    async def stop(self) -> None:
        self._stop_event.set()
        self._state.running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("SSE consumer shut down with an error")
        if self._owns_client:
            await self._client.aclose()

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._settings.sse_enabled,
            "running": self._state.running,
            "connected": self._state.connected,
            "reconnects": self._state.reconnects,
            "eventsReceived": self._state.events_received,
            "eventsDispatched": self._state.events_dispatched,
            "duplicatesIgnored": self._state.duplicates_ignored,
            "invalidEvents": self._state.invalid_events,
            "handlerErrors": self._state.handler_errors,
            "lastEventAt": self._state.last_event_at,
            "lastActivityAt": self._state.last_activity_at,
            "lastError": self._state.last_error,
        }

    # ------------------------------------------------------------------ main loop
    async def run(self) -> None:
        backoff = self._settings.sse_reconnect_initial_seconds
        while not self._stop_event.is_set():
            connected = False
            try:
                await self._consume_once()
                connected = self._state.connected
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._state.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("SSE connection failed: %s", self._state.last_error)
            finally:
                self._state.connected = False

            if self._stop_event.is_set():
                break
            if connected:
                backoff = self._settings.sse_reconnect_initial_seconds
            self._state.reconnects += 1
            delay = self._with_jitter(backoff)
            logger.info("SSE reconnecting in %.2fs", delay)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break
            except TimeoutError:
                pass
            backoff = min(
                backoff * 2,
                self._settings.sse_reconnect_max_seconds,
            )

    def _with_jitter(self, delay: float) -> float:
        # Full jitter keeps reconnect storms from synchronizing.
        return self._rng.uniform(delay * 0.5, delay * 1.5)

    async def _consume_once(self) -> None:
        parser = SSEFrameParser()
        async for line in self._stream_lines():
            if self._stop_event.is_set():
                return
            self._state.last_activity_at = isoformat(utc_now())
            event = parser.feed_line(line)
            if event is None:
                continue
            await self._handle_event(event)

    async def _stream_lines(self) -> AsyncIterator[str]:
        """Open the SSE stream and yield raw lines. Overridden by tests with a fake."""
        timeout = httpx.Timeout(
            connect=self._settings.sse_connect_timeout_seconds,
            read=self._settings.sse_read_timeout_seconds,
            write=self._settings.sse_connect_timeout_seconds,
            pool=self._settings.sse_connect_timeout_seconds,
        )
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        async with self._client.stream(
            "GET", self._settings.java_events_url, headers=headers, timeout=timeout
        ) as response:
            if response.status_code != 200:
                raise UpstreamError(
                    f"SSE endpoint returned status {response.status_code}",
                    code="SSE_UPSTREAM_ERROR",
                )
            self._state.connected = True
            logger.info("SSE connected to %s", self._settings.java_events_url)
            async for line in response.aiter_lines():
                yield line

    # ------------------------------------------------------------------ events
    async def _handle_event(self, event: SSEEvent) -> None:
        if event.event != "fetch.completed":
            logger.debug("Ignoring SSE event type %s", event.event)
            return

        if event.event_id:
            if event.event_id in self._seen_event_id_set:
                self._state.duplicates_ignored += 1
                logger.info("Ignoring duplicate SSE event %s", event.event_id)
                return
            if len(self._seen_event_ids) == self._seen_event_ids.maxlen:
                oldest = self._seen_event_ids.popleft()
                self._seen_event_id_set.discard(oldest)
            self._seen_event_ids.append(event.event_id)
            self._seen_event_id_set.add(event.event_id)

        self._state.events_received += 1
        try:
            payload = json.loads(event.data)
            parsed = FetchCompletedPayload.model_validate(payload)
            job_id = validate_job_id(parsed.job_id)
            expected_key = self._settings.manifest_key(job_id)
            if parsed.manifest_key != expected_key:
                raise ValueError(
                    f"manifestKey {parsed.manifest_key!r} does not match {expected_key!r}"
                )
        except Exception as exc:
            self._state.invalid_events += 1
            logger.warning("Ignoring invalid SSE fetch.completed event: %s", exc)
            return

        completed = FetchCompletedEvent(
            event_id=event.event_id or f"fetch:{job_id}:completed",
            job_id=job_id,
            manifest_key=expected_key,
        )
        self._state.last_event_at = isoformat(utc_now())
        self._state.events_dispatched += 1
        try:
            await self._handler(completed)
        except Exception as exc:
            self._state.handler_errors += 1
            self._state.last_error = f"handler: {type(exc).__name__}: {exc}"
            logger.exception("SSE handler failed for job %s", completed.job_id)


__all__ = [
    "ConsumerState",
    "FetchCompletedEvent",
    "FetchEventConsumer",
    "SSEEvent",
    "SSEFrameParser",
]
