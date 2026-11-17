"""Health/readiness routes (Milvus, MinIO, llama.cpp + SSE observability)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": container.settings.app_name,
        "version": container.settings.app_version,
    }


@router.get("/ready")
async def ready(container: AppContainer = Depends(get_container)) -> JSONResponse:
    checks = await asyncio.gather(
        container.vector_store.ping(),
        container.object_store.ping(),
        container.embeddings.ping(),
        return_exceptions=True,
    )
    names = ("milvus", "minio", "llamaCpp")
    components: dict[str, dict[str, Any]] = {}
    all_ready = True
    for name, result in zip(names, checks, strict=True):
        if isinstance(result, BaseException):
            all_ready = False
            components[name] = {
                "ready": False,
                "error": type(result).__name__,
                "message": _error_message(result),
            }
        else:
            components[name] = {"ready": True}

    sse = container.sse_consumer.snapshot()
    if not container.settings.background_tasks_enabled:
        sse["note"] = "background tasks disabled"
    components["sseConsumer"] = sse

    return JSONResponse(
        status_code=200 if all_ready else 503,
        content={
            "status": "ready" if all_ready else "not_ready",
            "components": components,
            "ingestionQueue": container.queue.status_snapshot(),
            "lastReconciliation": container.reconciler.last_report,
        },
    )


def _error_message(error: BaseException) -> str:
    message = getattr(error, "message", None)
    return str(message) if message else str(error)[:200]
