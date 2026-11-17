"""Image bytes endpoint: imageId -> Milvus object_key -> MinIO object."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_container
from app.container import AppContainer
from app.errors import NotFoundError, ObjectStoreError
from app.utils import validate_image_id

router = APIRouter(prefix="/api/v1", tags=["images"])

_CACHE_CONTROL = "public, max-age=86400"


@router.get("/images/{image_id}", response_class=Response)
async def get_image(
    image_id: str,
    container: AppContainer = Depends(get_container),
) -> Response:
    image_id = validate_image_id(image_id)
    reference = await container.vector_store.get_by_id(image_id)
    if reference is None or not reference.object_key:
        raise NotFoundError(f"image {image_id} was not found", code="IMAGE_NOT_FOUND")
    try:
        data = await container.object_store.get_bytes(reference.object_key)
    except ObjectStoreError as exc:
        if exc.status_code == 404:
            raise NotFoundError(
                f"image object for {image_id} is missing from storage",
                code="IMAGE_OBJECT_NOT_FOUND",
            ) from exc
        raise

    content_type = reference.content_type or "application/octet-stream"
    headers = {
        "Cache-Control": _CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
    }
    if reference.sha256:
        headers["ETag"] = f'"{reference.sha256}"'
    return Response(content=data, media_type=content_type, headers=headers)
