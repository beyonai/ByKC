"""Object storage helpers for knowledge base ingestion."""

import asyncio
from typing import Any

from botocore.exceptions import ClientError

_TRANSIENT_BUCKET_STATUS_CODES = {500, 502, 503, 504}
_MISSING_BUCKET_ERROR_CODES = {"404", "NoSuchBucket", "NotFound"}


class KnowledgeBaseObjectStorage:
    """Async S3-compatible object storage for knowledge base ingestion."""

    def __init__(
        self,
        *,
        session: Any,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket_name: str,
        markdown_bucket_name: str,
        bucket_ready_retries: int = 3,
        bucket_ready_retry_delay_seconds: float = 0.5,
    ):
        self.session = session
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.bucket_name = bucket_name
        self.markdown_bucket_name = markdown_bucket_name
        self.bucket_ready_retries = bucket_ready_retries
        self.bucket_ready_retry_delay_seconds = bucket_ready_retry_delay_seconds

    def _client(self):
        return self.session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        )

    def build_temp_object_key(self, import_request_id: str) -> str:
        return f"tmp/{import_request_id}/content.md"

    def build_original_object_key(
        self, *, knowledge_base_id, knowledge_item_id, version
    ):
        return f"kb/{knowledge_base_id}/item/{knowledge_item_id}/version/{version}/original"

    def build_markdown_object_key(
        self, *, knowledge_base_id, knowledge_item_id, version
    ):
        return f"kb/{knowledge_base_id}/item/{knowledge_item_id}/version/{version}/markdown"

    async def ensure_buckets(self) -> None:
        async with self._client() as s3:
            for bucket in (self.bucket_name, self.markdown_bucket_name):
                try:
                    await self._call_bucket_operation_with_retry(
                        s3.head_bucket, Bucket=bucket
                    )
                except Exception as exc:
                    if not self._is_missing_bucket_error(exc):
                        raise
                    await self._call_bucket_operation_with_retry(
                        s3.create_bucket, Bucket=bucket
                    )

    async def _call_bucket_operation_with_retry(self, operation, **kwargs):
        attempts = max(1, self.bucket_ready_retries)
        for attempt in range(attempts):
            try:
                return await operation(**kwargs)
            except ClientError as exc:
                if attempt >= attempts - 1 or not self._is_transient_bucket_error(exc):
                    raise
                await asyncio.sleep(self.bucket_ready_retry_delay_seconds)
        return None

    @staticmethod
    def _is_transient_bucket_error(exc: ClientError) -> bool:
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status_code in _TRANSIENT_BUCKET_STATUS_CODES

    @staticmethod
    def _is_missing_bucket_error(exc: Exception) -> bool:
        if not isinstance(exc, ClientError):
            return True
        error_code = exc.response.get("Error", {}).get("Code")
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return error_code in _MISSING_BUCKET_ERROR_CODES or status_code == 404

    async def upload_temp_object(
        self, import_request_id, content, *, content_type, bucket_name=None
    ):
        object_key = self.build_temp_object_key(import_request_id)
        async with self._client() as s3:
            await s3.put_object(
                Bucket=bucket_name or self.bucket_name,
                Key=object_key,
                Body=content,
                ContentType=content_type,
            )
        return object_key

    async def promote_temp_object(
        self, temp_object_key, final_object_key, *, bucket_name=None
    ):
        target = bucket_name or self.bucket_name
        async with self._client() as s3:
            await s3.copy_object(
                Bucket=target,
                Key=final_object_key,
                CopySource={"Bucket": target, "Key": temp_object_key},
            )
            await s3.delete_object(Bucket=target, Key=temp_object_key)

    async def delete_object_quietly(self, object_key, *, bucket_name=None):
        try:
            async with self._client() as s3:
                await s3.delete_object(
                    Bucket=bucket_name or self.bucket_name, Key=object_key
                )
        except Exception:
            return

    async def download_object(self, object_key, *, bucket_name=None):
        async with self._client() as s3:
            response = await s3.get_object(
                Bucket=bucket_name or self.bucket_name, Key=object_key
            )
            return await response["Body"].read()

    async def build_access_url(self, object_key, *, expires, bucket_name=None):
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name or self.bucket_name, "Key": object_key},
                ExpiresIn=int(expires.total_seconds()),
            )
