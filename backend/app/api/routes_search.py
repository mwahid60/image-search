"""Vector-only search route."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.api.schemas import SearchRequest
from app.container import AppContainer
from app.errors import InvalidRequestError
from app.services.search import SearchResponse

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    container: AppContainer = Depends(get_container),
) -> SearchResponse:
    if payload.top_k is not None and payload.top_k > container.settings.search_max_top_k:
        raise InvalidRequestError(
            f"topK must be at most {container.settings.search_max_top_k}",
            code="INVALID_TOP_K",
        )
    return await container.search_service.search(payload.query, payload.top_k)
