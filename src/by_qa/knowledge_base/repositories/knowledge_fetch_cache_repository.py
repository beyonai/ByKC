"""Persistence helpers for fetched-file cache index rows."""

from typing import Any


class KnowledgeFetchCacheRepository:
    """Repository for cache index rows backing fetched local files."""

    def upsert_cache_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        knowledge_item_id: int,
        knowledge_item_version_id: int,
        kb_code: str,
        full_path: str,
        virtual_path: str,
        bucket_name: str,
        object_key: str,
        checksum: str | None,
        cache_file_path: str,
        file_size: int | None,
        cache_ttl_seconds: int,
    ) -> dict[str, Any] | None:
        """Insert or refresh one local cache entry."""
        cursor.execute(
            """
            DELETE FROM knowledge_fetch_cache_index
            WHERE knowledge_item_version_id = %(knowledge_item_version_id)s
               OR cache_file_path = %(cache_file_path)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "knowledge_item_id": knowledge_item_id,
                "knowledge_item_version_id": knowledge_item_version_id,
                "kb_code": kb_code,
                "full_path": full_path,
                "virtual_path": virtual_path,
                "bucket_name": bucket_name,
                "object_key": object_key,
                "checksum": checksum,
                "cache_file_path": cache_file_path,
                "file_size": file_size,
                "cache_ttl_seconds": cache_ttl_seconds,
            },
        )
        cursor.execute(
            """
            INSERT INTO knowledge_fetch_cache_index (
                knowledge_base_id,
                fs_entry_id,
                knowledge_item_id,
                knowledge_item_version_id,
                kb_code,
                full_path,
                virtual_path,
                bucket_name,
                object_key,
                checksum,
                cache_file_path,
                file_size,
                cache_ttl_seconds,
                first_cached_at,
                last_cached_at,
                last_accessed_at,
                expires_at,
                cache_status,
                evict_retry_count,
                last_error,
                created_at,
                updated_at
            )
            VALUES (
                %(knowledge_base_id)s,
                %(fs_entry_id)s,
                %(knowledge_item_id)s,
                %(knowledge_item_version_id)s,
                %(kb_code)s,
                %(full_path)s,
                %(virtual_path)s,
                %(bucket_name)s,
                %(object_key)s,
                %(checksum)s,
                %(cache_file_path)s,
                %(file_size)s,
                %(cache_ttl_seconds)s,
                NOW(),
                NOW(),
                NOW(),
                NOW() + (%(cache_ttl_seconds)s * INTERVAL '1 second'),
                'READY',
                0,
                NULL,
                NOW(),
                NOW()
            )
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "knowledge_item_id": knowledge_item_id,
                "knowledge_item_version_id": knowledge_item_version_id,
                "kb_code": kb_code,
                "full_path": full_path,
                "virtual_path": virtual_path,
                "bucket_name": bucket_name,
                "object_key": object_key,
                "checksum": checksum,
                "cache_file_path": cache_file_path,
                "file_size": file_size,
                "cache_ttl_seconds": cache_ttl_seconds,
            },
        )
        cursor.execute(
            """
            SELECT kid
            FROM knowledge_fetch_cache_index
            WHERE knowledge_item_version_id = %(knowledge_item_version_id)s
            """,
            {"knowledge_item_version_id": knowledge_item_version_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def get_by_version_id(
        self, cursor: Any, *, knowledge_item_version_id: int
    ) -> dict[str, Any] | None:
        """Fetch the cache row for one item version."""
        cursor.execute(
            """
            SELECT *
            FROM knowledge_fetch_cache_index
            WHERE knowledge_item_version_id = %(knowledge_item_version_id)s
            """,
            {"knowledge_item_version_id": knowledge_item_version_id},
        )
        fetchone = getattr(cursor, "fetchone", None)
        return fetchone() if callable(fetchone) else None

    def touch_cache_entry(
        self, cursor: Any, *, cache_entry_id: int, cache_ttl_seconds: int
    ) -> None:
        """Extend one cache row after a successful cache hit."""
        cursor.execute(
            """
            UPDATE knowledge_fetch_cache_index
            SET last_accessed_at = NOW(),
                expires_at = NOW() + (%(cache_ttl_seconds)s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE kid = %(cache_entry_id)s
            """,
            {"cache_entry_id": cache_entry_id, "cache_ttl_seconds": cache_ttl_seconds},
        )

    def mark_expired_ready_entries_as_evicting(
        self, cursor: Any, *, batch_size: int
    ) -> None:
        """Promote a bounded batch of expired READY rows into EVICTING."""
        cursor.execute(
            """
            UPDATE knowledge_fetch_cache_index
            SET cache_status = 'EVICTING',
                updated_at = NOW()
            WHERE kid IN (
                SELECT kid
                FROM knowledge_fetch_cache_index
                WHERE cache_status = 'READY'
                  AND expires_at <= NOW()
                ORDER BY expires_at
                LIMIT %(batch_size)s
                FOR UPDATE SKIP LOCKED
            )
            """,
            {"batch_size": batch_size},
        )

    def list_cleanup_candidates(
        self, cursor: Any, *, batch_size: int
    ) -> list[dict[str, Any]]:
        """Load rows that should be retried by the cleanup worker."""
        cursor.execute(
            """
            SELECT *
            FROM knowledge_fetch_cache_index
            WHERE cache_status IN ('EVICTING', 'ERROR')
            ORDER BY expires_at
            LIMIT %(batch_size)s
            FOR UPDATE SKIP LOCKED
            """,
            {"batch_size": batch_size},
        )
        fetchall = getattr(cursor, "fetchall", None)
        return list(fetchall()) if callable(fetchall) else []

    def delete_cache_entry(self, cursor: Any, *, cache_entry_id: int) -> None:
        """Delete one cache row after successful eviction."""
        cursor.execute(
            """
            DELETE FROM knowledge_fetch_cache_index
            WHERE kid = %(cache_entry_id)s
            """,
            {"cache_entry_id": cache_entry_id},
        )

    def mark_cache_entry_error(
        self, cursor: Any, *, cache_entry_id: int, error: str
    ) -> None:
        """Persist a failed eviction attempt for retry in the next cleanup cycle."""
        cursor.execute(
            """
            UPDATE knowledge_fetch_cache_index
            SET cache_status = 'ERROR',
                evict_retry_count = evict_retry_count + 1,
                last_error = %(error)s,
                updated_at = NOW()
            WHERE kid = %(cache_entry_id)s
            """,
            {"cache_entry_id": cache_entry_id, "error": error},
        )
