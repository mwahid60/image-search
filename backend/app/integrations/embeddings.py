"""llama.cpp /v1/embeddings client for EmbeddingGemma 2 (text and image).

Image requests contain ONLY the image bytes as a typed `image_url` content part.
No title, tags, class name, caption, or evaluation metadata is ever sent to the model.
"""

from __future__ import annotations

import asyncio
import base64
import math
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import EmbeddingError
from app.logging_config import get_logger

logger = get_logger(__name__)

_ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class EmbeddingsClient(Protocol):
    async def embed_image(self, image_bytes: bytes, content_type: str) -> list[float]: ...

    async def embed_text(self, query: str) -> list[float]: ...

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


SleepFunc = Callable[[float], Awaitable[None]]


class LlamaCppEmbeddingsClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        sleep: SleepFunc | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.llama_base_url,
            timeout=httpx.Timeout(settings.llama_timeout_seconds),
        )
        self._sleep: SleepFunc = sleep or asyncio.sleep

    def image_payload(self, image_bytes: bytes, content_type: str) -> dict[str, Any]:
        mime = (content_type or "").split(";")[0].strip().lower()
        if mime not in _ALLOWED_IMAGE_MIME:
            raise EmbeddingError(
                f"Unsupported image content type for embedding: {mime or 'unknown'}",
                code="UNSUPPORTED_IMAGE_TYPE",
                status_code=422,
            )
        if not image_bytes:
            raise EmbeddingError("Image bytes are empty", code="INVALID_IMAGE", status_code=422)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return {
            "model": self._settings.llama_model,
            "input": [
                {
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        }
                    ]
                }
            ],
        }

    def text_payload(self, query: str) -> dict[str, Any]:
        prefix = self._settings.query_prompt_prefix
        cleaned = query.strip()
        separator = "" if not prefix or prefix.endswith(" ") else " "
        text = f"{prefix}{separator}{cleaned}"
        return {
            "model": self._settings.llama_model,
            "input": [{"content": [{"type": "text", "text": text}]}],
        }

    async def embed_image(self, image_bytes: bytes, content_type: str) -> list[float]:
        return await self._embed(self.image_payload(image_bytes, content_type))

    async def embed_text(self, query: str) -> list[float]:
        query = (query or "").strip()
        if not query:
            raise EmbeddingError("Query must not be empty", code="INVALID_QUERY", status_code=400)
        return await self._embed(self.text_payload(query))

    async def _embed(self, payload: dict[str, Any]) -> list[float]:
        """POST embeddings with bounded retries for transient failures.

        Retried: HTTP 503, timeouts, and connection/transport errors.
        Not retried: other HTTP statuses (including 400/422) and malformed
        responses. The request payload (image base64) is never logged.
        """
        attempts = self._settings.embedding_max_attempts
        backoff = min(
            self._settings.embedding_retry_initial_seconds,
            self._settings.embedding_retry_max_seconds,
        )
        last_error: EmbeddingError | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post("/v1/embeddings", json=payload)
            except httpx.TimeoutException:
                last_error = EmbeddingError(
                    f"Embedding request timed out after {attempt} attempt(s)",
                    code="EMBEDDING_TIMEOUT",
                    status_code=504,
                )
            except httpx.TransportError:
                last_error = EmbeddingError(
                    f"Embedding service is unavailable (attempt {attempt}/{attempts})",
                    code="EMBEDDING_UNAVAILABLE",
                    status_code=503,
                )
            except httpx.HTTPError as exc:
                raise EmbeddingError(
                    "Embedding request failed",
                    code="EMBEDDING_UNAVAILABLE",
                    status_code=503,
                ) from exc
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise EmbeddingError(
                            "Embedding response is not valid JSON",
                            code="INVALID_EMBEDDING_RESPONSE",
                        ) from exc
                    return self._parse_vector(body)
                if response.status_code == 503:
                    last_error = EmbeddingError(
                        f"Embedding service returned status 503 after {attempt} attempt(s)",
                        code="EMBEDDING_UNAVAILABLE",
                        status_code=503,
                    )
                else:
                    logger.warning(
                        "llama.cpp embeddings returned non-retryable status %s",
                        response.status_code,
                    )
                    raise EmbeddingError(
                        f"Embedding service returned status {response.status_code}",
                        code="EMBEDDING_UPSTREAM_ERROR",
                        status_code=502,
                    )

            if attempt == attempts:
                break
            logger.warning(
                "Embedding attempt %d/%d failed (%s); retrying in %.2fs",
                attempt,
                attempts,
                last_error.code if last_error else "UNKNOWN",
                backoff,
            )
            await self._sleep(backoff)
            backoff = min(backoff * 2, self._settings.embedding_retry_max_seconds)

        if last_error is None:  # pragma: no cover - defensive guard
            raise EmbeddingError("Embedding request failed", code="EMBEDDING_ERROR")
        raise last_error

    def _parse_vector(self, body: Any) -> list[float]:
        vector: Any = None
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    vector = first.get("embedding")
            if vector is None:
                # Older llama.cpp builds expose a flat "embeddings" array.
                embeddings = body.get("embeddings")
                if isinstance(embeddings, list) and embeddings:
                    vector = embeddings[0]
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(
                "Embedding response does not contain a vector",
                code="INVALID_EMBEDDING_RESPONSE",
            )
        expected = self._settings.embedding_dimension
        if len(vector) != expected:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {expected}, got {len(vector)}",
                code="INVALID_EMBEDDING_DIMENSION",
            )
        parsed: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise EmbeddingError(
                    "Embedding contains a non-numeric value",
                    code="INVALID_EMBEDDING_VALUE",
                )
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingError(
                    "Embedding contains NaN or infinity", code="INVALID_EMBEDDING_VALUE"
                )
            parsed.append(number)
        if math.fsum(abs(value) for value in parsed) == 0.0:
            raise EmbeddingError("Embedding vector is all zeros", code="INVALID_EMBEDDING_VALUE")
        return parsed

    async def ping(self) -> None:
        try:
            response = await self._client.get(
                "/health", timeout=min(self._settings.llama_timeout_seconds, 5.0)
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                "Embedding service is unreachable", code="EMBEDDING_UNAVAILABLE", status_code=503
            ) from exc
        if response.status_code != 200:
            raise EmbeddingError(
                f"Embedding service health returned {response.status_code}",
                code="EMBEDDING_UNAVAILABLE",
                status_code=503,
            )

    async def close(self) -> None:
        await self._client.aclose()
