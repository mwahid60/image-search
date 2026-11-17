"""Ingestion status model persisted at jobs/bank-mas/{jobId}/ingestion-status.json."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.models.manifest import CamelModel


class IngestionState(StrEnum):
    PENDING = "PENDING"
    INGESTING = "INGESTING"
    INDEXED = "INDEXED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class IngestionError(CamelModel):
    image_id: str | None = None
    object_key: str | None = None
    stage: str
    reason: str


class IngestionStatus(CamelModel):
    job_id: str
    status: IngestionState
    total_images: int = 0
    indexed: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[IngestionError] = Field(default_factory=list)
    attempts: int = Field(default=0, ge=0)
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def usable(self) -> int:
        return self.indexed + self.duplicates

    @property
    def effective_state(self) -> IngestionState:
        """Treat legacy `INDEXED` records with failures as unfinished work."""
        if self.status == IngestionState.INDEXED and self.failed > 0:
            return IngestionState.PARTIAL if self.usable > 0 else IngestionState.FAILED
        return self.status

    def with_errors(self, errors: list[IngestionError]) -> IngestionStatus:
        return self.model_copy(update={"errors": errors, "failed": len(errors)})
