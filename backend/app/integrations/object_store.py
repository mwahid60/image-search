"""MinIO/S3 object storage access behind a small protocol.

The protocol is intentionally narrow so unit tests can use an in-memory fake.
"""

from __future__ import annotations

import asyncio
import io
from typing import Protocol

import urllib3
from minio import Minio
from minio.error import S3Error

from app.config import Settings
from app.errors import ObjectNotFoundError, ObjectStoreError
from app.logging_config import get_logger

logger = get_logger(__name__)

_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchObject", "NoSuchBucket", "NoSuchVersion"}


class ObjectStore(Protocol):
    async def get_bytes(self, key: str) -> bytes: ...

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def list_keys(self, prefix: str) -> list[str]: ...

    async def ensure_bucket(self) -> None: ...

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


class MinioObjectStore:
    def __init__(self, settings: Settings, client: Minio | None = None) -> None:
        self._settings = settings
        self._client = client or Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(
                    connect=settings.minio_timeout_seconds,
                    read=settings.minio_timeout_seconds,
                ),
                retries=urllib3.Retry(
                    total=settings.minio_max_retries,
                    backoff_factor=0.2,
                    status_forcelist=[500, 502, 503, 504],
                ),
            ),
        )
        self._bucket = settings.minio_bucket

    async def _call(self, fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except S3Error as exc:
            if exc.code in _NOT_FOUND_CODES:
                object_name = kwargs.get("object_name") or (args[1] if len(args) > 1 else "?")
                raise ObjectNotFoundError(
                    f"MinIO object not found: {object_name}",
                    details={"minioCode": exc.code},
                ) from exc
            logger.warning("MinIO error: %s", exc)
            raise ObjectStoreError("MinIO request failed", details={"minioCode": exc.code}) from exc
        except ObjectStoreError:
            raise
        except Exception as exc:
            logger.warning("MinIO unavailable: %s", exc)
            raise ObjectStoreError("MinIO is unavailable") from exc

    async def get_bytes(self, key: str) -> bytes:
        response = await self._call(self._client.get_object, self._bucket, key)
        try:
            data = await asyncio.to_thread(response.read)
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)
        return data

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        await self._call(
            self._client.put_object,
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    async def exists(self, key: str) -> bool:
        try:
            await self._call(self._client.stat_object, self._bucket, key)
            return True
        except ObjectNotFoundError:
            return False

    async def list_keys(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            return [
                obj.object_name
                for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
                if obj.object_name
            ]

        return await self._call(_list)

    async def ensure_bucket(self) -> None:
        if not self._settings.minio_ensure_bucket:
            return

        def _ensure() -> None:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await self._call(_ensure)

    async def ping(self) -> None:
        def _ping() -> None:
            if not self._client.bucket_exists(self._bucket):
                raise ObjectStoreError("MinIO bucket is missing")

        await self._call(_ping)

    async def close(self) -> None:
        # minio-py has no close; kept for symmetry with other clients.
        return None
