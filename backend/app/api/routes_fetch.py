"""Fetch job proxy + aggregated status routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.api.schemas import FetchJobAcceptedResponse, FetchJobRequest
from app.container import AppContainer
from app.errors import InvalidRequestError
from app.services.fetch_status import FetchJobStatus

router = APIRouter(prefix="/api/v1", tags=["fetch"])


@router.post(
    "/fetch-jobs",
    status_code=202,
    response_model=FetchJobAcceptedResponse,
)
async def create_fetch_job(
    payload: FetchJobRequest,
    container: AppContainer = Depends(get_container),
) -> FetchJobAcceptedResponse:
    settings = container.settings
    if len(payload.classes) > settings.max_classes_per_request:
        raise InvalidRequestError(
            f"at most {settings.max_classes_per_request} classes are allowed per request",
            code="TOO_MANY_CLASSES",
        )
    if payload.images_per_class > settings.max_images_per_class:
        raise InvalidRequestError(
            f"imagesPerClass must be at most {settings.max_images_per_class}",
            code="INVALID_IMAGES_PER_CLASS",
        )
    accepted = await container.java_client.create_fetch_job(
        payload.classes, payload.images_per_class
    )
    return FetchJobAcceptedResponse(job_id=accepted.job_id, status=accepted.status)


@router.get("/fetch-jobs/{job_id}", response_model=FetchJobStatus)
async def get_fetch_job(
    job_id: str,
    container: AppContainer = Depends(get_container),
) -> FetchJobStatus:
    return await container.fetch_status_service.get_status(job_id)
