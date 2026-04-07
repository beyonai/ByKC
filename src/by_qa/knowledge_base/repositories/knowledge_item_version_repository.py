"""Persistence helpers for knowledge_item_version rows."""

from typing import Any


class KnowledgeItemVersionRepository:
    """Repository for knowledge item versions."""

    def upsert(
        self,
        cursor: Any,
        *,
        knowledge_item_id: int,
        fs_entry_id: int,
        version: str,
        bucket_name: str,
        object_key: str,
        markdown_bucket_name: str | None,
        markdown_object_key: str | None,
        markdown_file_size: int | None,
        markdown_checksum: str | None,
        file_size: int,
        checksum: str | None,
    ) -> dict[str, Any] | None:
        """Insert or update a knowledge item version."""
        cursor.execute(
            """
            MERGE INTO knowledge_item_version AS target
            USING (
                SELECT
                    %(knowledge_item_id)s AS knowledge_item_id,
                    %(fs_entry_id)s AS fs_entry_id,
                    %(version)s AS version,
                    %(bucket_name)s AS bucket_name,
                    %(object_key)s AS object_key,
                    %(markdown_bucket_name)s AS markdown_bucket_name,
                    %(markdown_object_key)s AS markdown_object_key,
                    %(markdown_file_size)s AS markdown_file_size,
                    %(markdown_checksum)s AS markdown_checksum,
                    %(file_size)s AS file_size,
                    %(checksum)s AS checksum
            ) AS source
            ON (
                target.knowledge_item_id = source.knowledge_item_id
                AND target.version = source.version
            )
            WHEN MATCHED THEN
                UPDATE SET
                    bucket_name = source.bucket_name,
                    object_key = source.object_key,
                    markdown_bucket_name = source.markdown_bucket_name,
                    markdown_object_key = source.markdown_object_key,
                    markdown_file_size = source.markdown_file_size,
                    markdown_checksum = source.markdown_checksum,
                    file_size = source.file_size,
                    checksum = source.checksum,
                    updated_at = NOW()
            WHEN NOT MATCHED THEN
                INSERT (
                    knowledge_item_id,
                    fs_entry_id,
                    version,
                    bucket_name,
                    object_key,
                    markdown_bucket_name,
                    markdown_object_key,
                    markdown_file_size,
                    markdown_checksum,
                    file_size,
                    checksum,
                    created_at,
                    updated_at
                )
                VALUES (
                    source.knowledge_item_id,
                    source.fs_entry_id,
                    source.version,
                    source.bucket_name,
                    source.object_key,
                    source.markdown_bucket_name,
                    source.markdown_object_key,
                    source.markdown_file_size,
                    source.markdown_checksum,
                    source.file_size,
                    source.checksum,
                    NOW(),
                    NOW()
                )
            """,
            {
                "knowledge_item_id": knowledge_item_id,
                "fs_entry_id": fs_entry_id,
                "version": version,
                "bucket_name": bucket_name,
                "object_key": object_key,
                "markdown_bucket_name": markdown_bucket_name,
                "markdown_object_key": markdown_object_key,
                "markdown_file_size": markdown_file_size,
                "markdown_checksum": markdown_checksum,
                "file_size": file_size,
                "checksum": checksum,
            },
        )
        cursor.execute(
            """
            SELECT kid
            FROM knowledge_item_version
            WHERE knowledge_item_id = %(knowledge_item_id)s
              AND version = %(version)s
            """,
            {"knowledge_item_id": knowledge_item_id, "version": version},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def get_by_item_and_version(
        self, cursor: Any, *, knowledge_item_id: int, version: str
    ) -> dict[str, Any] | None:
        """Fetch an existing version row by knowledge_item_id and version."""
        cursor.execute(
            """
            SELECT
                kid,
                knowledge_item_id,
                version,
                bucket_name,
                object_key,
                markdown_bucket_name,
                markdown_object_key,
                markdown_file_size,
                markdown_checksum,
                file_size,
                checksum
            FROM knowledge_item_version
            WHERE knowledge_item_id = %(knowledge_item_id)s
              AND version = %(version)s
            """,
            {"knowledge_item_id": knowledge_item_id, "version": version},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None
