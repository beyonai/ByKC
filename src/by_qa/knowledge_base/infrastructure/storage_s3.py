"""Default S3/MinIO-backed implementation of KnowledgeStorageProvider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from botocore.exceptions import ClientError

from by_qa.knowledge_base.infrastructure.object_storage import (
    KnowledgeBaseObjectStorage,
)
from by_qa.knowledge_base.infrastructure.storage import (
    StorageAuthenticationError,
    StorageConflictError,
    StorageError,
    StorageLocation,
    StorageNotFoundError,
    StorageOperationError,
    StoredObject,
)

_NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}
_MISSING_BUCKET_CODES = {"NoSuchBucket"}
_AUTH_CODES = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}
_CONFLICT_CODES = {"PreconditionFailed", "BucketAlreadyOwnedByYou"}


@dataclass
class S3KnowledgeStorageProvider:
    """Wrap KnowledgeBaseObjectStorage and expose the standard storage protocol."""

    storage: KnowledgeBaseObjectStorage
    provider_name: str = "minio"
    storage_path_bound_to_logical_path: bool = False

    async def ensure_ready(self) -> None:
        await self.storage.ensure_buckets()

    def build_original_location(
        self,
        *,
        kb_code: str,
        knowledge_base_id: int,
        fs_entry_id: int,
        file_path: str,
        mime_type: str,
    ) -> StorageLocation:
        _ = kb_code, mime_type
        suffix = PurePosixPath(file_path.strip("/")).suffix
        return StorageLocation(
            namespace=self.storage.bucket_name,
            key=f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/original{suffix}",
        )

    def build_markdown_location(
        self,
        *,
        kb_code: str,
        knowledge_base_id: int,
        fs_entry_id: int,
        file_path: str,
    ) -> StorageLocation:
        _ = kb_code, file_path
        return StorageLocation(
            namespace=self.storage.markdown_bucket_name,
            key=f"kb/{knowledge_base_id}/fs-entry/{fs_entry_id}/markdown.md",
        )

    async def write(
        self,
        location: StorageLocation,
        content: bytes,
        *,
        content_type: str,
    ) -> StoredObject:
        try:
            async with self.storage._client() as s3:  # noqa: SLF001
                await s3.put_object(
                    Bucket=location.namespace,
                    Key=location.key,
                    Body=content,
                    ContentType=content_type,
                )
        except ClientError as exc:
            raise self._translate(exc, "write") from exc
        return StoredObject(
            location=location,
            size=len(content),
            content_type=content_type,
        )

    async def read(self, location: StorageLocation) -> bytes:
        try:
            async with self.storage._client() as s3:  # noqa: SLF001
                response = await s3.get_object(
                    Bucket=location.namespace, Key=location.key
                )
                return await response["Body"].read()
        except ClientError as exc:
            raise self._translate(exc, "read") from exc

    async def delete(self, location: StorageLocation) -> None:
        try:
            async with self.storage._client() as s3:  # noqa: SLF001
                await s3.delete_object(Bucket=location.namespace, Key=location.key)
        except ClientError as exc:
            err = self._translate(exc, "delete")
            if isinstance(err, StorageNotFoundError):
                if self._is_missing_bucket(exc):
                    raise err from exc
                return  # delete is idempotent for missing key
            raise err from exc

    async def delete_quietly(self, location: StorageLocation) -> None:
        try:
            await self.delete(location)
        except StorageError:
            return

    async def move(
        self,
        source: StorageLocation,
        target: StorageLocation,
        *,
        overwrite: bool = False,
    ) -> None:
        try:
            async with self.storage._client() as s3:  # noqa: SLF001
                if not overwrite:
                    try:
                        await s3.head_object(Bucket=target.namespace, Key=target.key)
                    except ClientError as head_exc:
                        if not self._is_not_found(head_exc):
                            raise self._translate(head_exc, "move") from head_exc
                    else:
                        raise StorageConflictError(
                            f"target already exists: {target.namespace}/{target.key}"
                        )
                await s3.copy_object(
                    Bucket=target.namespace,
                    Key=target.key,
                    CopySource={"Bucket": source.namespace, "Key": source.key},
                )
                await s3.delete_object(Bucket=source.namespace, Key=source.key)
        except ClientError as exc:
            raise self._translate(exc, "move") from exc

    @staticmethod
    def _translate(exc: ClientError, op: str) -> StorageError:
        error_code = exc.response.get("Error", {}).get("Code") or ""
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        message = f"s3.{op}: {error_code or status}"
        if error_code in _NOT_FOUND_CODES or status == 404:
            return StorageNotFoundError(message)
        if error_code in _MISSING_BUCKET_CODES:
            return StorageNotFoundError(message)
        if error_code in _AUTH_CODES or status in {401, 403}:
            return StorageAuthenticationError(message)
        if error_code in _CONFLICT_CODES or status == 412:
            return StorageConflictError(message)
        return StorageOperationError(message)

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        error_code = exc.response.get("Error", {}).get("Code") or ""
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return error_code in _NOT_FOUND_CODES or status == 404

    @staticmethod
    def _is_missing_bucket(exc: ClientError) -> bool:
        error_code = exc.response.get("Error", {}).get("Code") or ""
        return error_code in _MISSING_BUCKET_CODES


def build_s3_storage_provider(
    settings: Any | None = None,
) -> S3KnowledgeStorageProvider:
    """Build the default MinIO/S3 provider for load_storage_provider()."""
    import aioboto3

    from by_qa.config import get_settings

    resolved_settings = settings or get_settings()
    scheme = "https" if resolved_settings.kb_minio_secure else "http"
    endpoint = resolved_settings.kb_minio_endpoint.removeprefix("http://").removeprefix(
        "https://"
    )
    endpoint_url = f"{scheme}://{endpoint}"

    storage = KnowledgeBaseObjectStorage(
        session=aioboto3.Session(),
        endpoint_url=endpoint_url,
        access_key=resolved_settings.kb_minio_access_key,
        secret_key=resolved_settings.kb_minio_secret_key,
        secure=resolved_settings.kb_minio_secure,
        bucket_name=resolved_settings.kb_minio_bucket,
        markdown_bucket_name=resolved_settings.kb_minio_markdown_bucket,
    )
    return S3KnowledgeStorageProvider(storage=storage)
