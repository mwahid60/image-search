"""FastAPI application factory for the Bank MAS image search backend."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes_fetch import router as fetch_router
from app.api.routes_health import router as health_router
from app.api.routes_images import router as images_router
from app.api.routes_ingest import router as ingest_router
from app.api.routes_search import router as search_router
from app.config import Settings, get_settings
from app.container import AppContainer, build_container
from app.errors import AppError
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Starting %s %s (milvus=%s, sse=%s, reconciliation=%s)",
            settings.app_name,
            settings.app_version,
            settings.milvus_uri,
            settings.sse_enabled,
            settings.reconciliation_enabled,
        )
        await container.start()
        try:
            yield
        finally:
            logger.info("Shutting down %s", settings.app_name)
            await container.stop()

    app = FastAPI(
        title="Bank MAS Image Search Backend",
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.container = container

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Request failed: %s (%s)", exc.message, exc.code)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "request validation failed",
                    "details": details,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "internal server error",
                }
            },
        )

    app.include_router(health_router)
    app.include_router(fetch_router)
    app.include_router(ingest_router)
    app.include_router(search_router)
    app.include_router(images_router)
    return app


app = create_app()


def main() -> None:
    """Entry point: `uv run python -m app.main` starts the API server."""
    import uvicorn

    logging.getLogger(__name__).info("Starting uvicorn")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
