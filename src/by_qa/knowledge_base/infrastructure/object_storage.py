"""Object storage helpers for knowledge base ingestion."""

from datetime import timedelta
from io import BytesIO
from typing import Any

try:
    from minio.commonconfig import CopySource
except ImportError:  # pragma: no cover
    CopySource = None


class KnowledgeBaseObjectStorage:
    """Thin wrapper around MinIO operations used by knowledge base ingestion."""

    def __init__(self, *, client: Any, bucket_name: str, markdown_bucket_name: str):
        self.client = client
        self.bucket_name = bucket_name
        self.markdown_bucket_name = markdown_bucket_name

    def build_temp_object_key(self, import_request_id: str) -> str:
        return f"tmp/{import_request_id}/content.md"

    def build_original_object_key(
        self,
        *,
        knowledge_base_id: int,
        knowledge_item_id: int,
        version: str,
    ) -> str:
        return f"kb/{knowledge_base_id}/item/{knowledge_item_id}/version/{version}/original"

    def build_markdown_object_key(
        self,
        *,
        knowledge_base_id: int,
        knowledge_item_id: int,
        version: str,
    ) -> str:
        return f"kb/{knowledge_base_id}/item/{knowledge_item_id}/version/{version}/markdown"

    def upload_temp_object(
        self,
        import_request_id: str,
        content: bytes,
        *,
        content_type: str,
        bucket_name: str | None = None,
    ) -> str:
        object_key = self.build_temp_object_key(import_request_id)
        payload = BytesIO(content)
        self.client.put_object(
            bucket_name or self.bucket_name,
            object_key,
            payload,
            length=len(content),
            content_type=content_type,
        )
        return object_key

    def promote_temp_object(
        self,
        temp_object_key: str,
        final_object_key: str,
        *,
        bucket_name: str | None = None,
    ) -> None:
        target_bucket_name = bucket_name or self.bucket_name
        source = (
            CopySource(target_bucket_name, temp_object_key)
            if CopySource is not None
            else f"/{target_bucket_name}/{temp_object_key}"
        )
        self.client.copy_object(target_bucket_name, final_object_key, source)
        self.client.remove_object(target_bucket_name, temp_object_key)

    def delete_object_quietly(
        self, object_key: str, *, bucket_name: str | None = None
    ) -> None:
        try:
            self.client.remove_object(bucket_name or self.bucket_name, object_key)
        except Exception:
            return

    def download_object(
        self, object_key: str, *, bucket_name: str | None = None
    ) -> bytes:
        response = self.client.get_object(bucket_name or self.bucket_name, object_key)
        try:
            return response.read()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            release_conn = getattr(response, "release_conn", None)
            if callable(release_conn):
                release_conn()

    def build_access_url(
        self,
        object_key: str,
        *,
        expires: timedelta,
        bucket_name: str | None = None,
    ) -> str:
        return str(
            self.client.presigned_get_object(
                bucket_name or self.bucket_name,
                object_key,
                expires=expires,
            )
        )
