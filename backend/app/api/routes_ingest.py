"""Manual ingestion retry route (fallback for missed SSE events / failures)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.api.schemas import IngestAcceptedResponse
from app.container import AppContainer
from app.errors import ManifestError
from app.utils import validate_job_id

router = APIRouter(prefix="/api/v1", tags=["ingestion"])


@router.post("/ingest/{job_id}", status_code=202, response_model=IngestAcceptedResponse)
async def retry_ingestion(
    job_id: str,
    container: AppContainer = Depends(get_container),
) -> IngestAcceptedResponse:
    validate_job_id(job_id)
    manifest_key = container.settings.manifest_key(job_id)
    if not await container.object_store.exists(manifest_key):
        raise ManifestError(
            f"fetch manifest not found for job {job_id}",
            code="MANIFEST_NOT_FOUND",
            status_code=404,
        )
    result = await container.queue.enqueue(job_id, manifest_key=manifest_key, force=True)
    status = "ENQUEUED" if result.enqueued else "ALREADY_RUNNING"
    return IngestAcceptedResponse(job_id=job_id, status=status, reason=result.reason)
