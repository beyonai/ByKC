"""Object storage helpers for knowledge base ingestion."""

from typing import Any


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
    ):
        self.session = session
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.bucket_name = bucket_name
        self.markdown_bucket_name = markdown_bucket_name

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
                    await s3.head_bucket(Bucket=bucket)
                except Exception:
                    await s3.create_bucket(Bucket=bucket)

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
