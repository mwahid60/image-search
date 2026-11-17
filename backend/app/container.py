"""Application container wiring integrations, ingestion and services together."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.queue import IngestionQueue
from app.ingestion.reconciliation import ReconciliationService
from app.integrations.embeddings import EmbeddingsClient, LlamaCppEmbeddingsClient
from app.integrations.java import JavaFetcherClient
from app.integrations.object_store import MinioObjectStore, ObjectStore
from app.integrations.sse import FetchCompletedEvent, FetchEventConsumer
from app.integrations.vector_store import MilvusVectorStore, VectorStore
from app.logging_config import get_logger
from app.services.fetch_status import FetchStatusService
from app.services.search import SearchService
from app.utils import Clock

logger = get_logger(__name__)


@dataclass
class AppContainer:
    """Owns long-lived clients and background tasks.

    Integrations can be injected (tests use fakes); missing ones are built from
    settings so production wiring stays declarative.
    """

    settings: Settings
    java_client: JavaFetcherClient | None = None
    object_store: ObjectStore | None = None
    embeddings: EmbeddingsClient | None = None
    vector_store: VectorStore | None = None
    clock: Clock = field(default_factory=Clock)

    def __post_init__(self) -> None:
        self.java_client = self.java_client or JavaFetcherClient(self.settings)
        self.object_store = self.object_store or MinioObjectStore(self.settings)
        self.embeddings = self.embeddings or LlamaCppEmbeddingsClient(self.settings)
        self.vector_store = self.vector_store or MilvusVectorStore(self.settings)

        self.pipeline = IngestionPipeline(
            self.settings,
            self.object_store,
            self.embeddings,
            self.vector_store,
            clock=self.clock,
        )
        self.queue = IngestionQueue(self.settings, self.pipeline)
        self.sse_consumer = FetchEventConsumer(self.settings, self._handle_fetch_completed)
        self.reconciler = ReconciliationService(
            self.settings, self.object_store, self.queue, self.pipeline
        )
        self.fetch_status_service = FetchStatusService(
            self.settings, self.java_client, self.object_store, self.pipeline
        )
        self.search_service = SearchService(self.settings, self.embeddings, self.vector_store)

    async def _handle_fetch_completed(self, event: FetchCompletedEvent) -> None:
        result = await self.queue.enqueue(event.job_id, manifest_key=event.manifest_key)
        logger.info(
            "SSE fetch.completed for job %s (eventId=%s) -> enqueued=%s reason=%s",
            event.job_id,
            event.event_id,
            result.enqueued,
            result.reason,
        )

    async def start(self) -> None:
        try:
            await self.vector_store.ensure_ready()
        except Exception as exc:
            logger.error("Milvus initialization failed: %s", exc)
        try:
            await self.object_store.ensure_bucket()
        except Exception as exc:
            logger.error("MinIO bucket initialization failed: %s", exc)

        if not self.settings.background_tasks_enabled:
            logger.info("Background tasks disabled by configuration")
            return
        await self.queue.start()
        if self.settings.sse_enabled:
            await self.sse_consumer.start()
        if self.settings.reconciliation_enabled:
            await self.reconciler.start()

    async def stop(self) -> None:
        for name, stopper in (
            ("reconciliation", self.reconciler.stop),
            ("sse-consumer", self.sse_consumer.stop),
            ("ingestion-queue", self.queue.stop),
        ):
            try:
                await stopper()
            except Exception:
                logger.exception("Error while stopping %s", name)
        for name, closer in (
            ("java-client", self.java_client.close),
            ("embeddings", self.embeddings.close),
            ("vector-store", self.vector_store.close),
            ("object-store", self.object_store.close),
        ):
            try:
                await closer()
            except Exception:
                logger.exception("Error while closing %s", name)


def build_container(settings: Settings) -> AppContainer:
    return AppContainer(settings=settings)
