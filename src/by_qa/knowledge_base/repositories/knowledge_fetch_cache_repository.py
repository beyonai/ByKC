"""Persistence helpers for fetched-file cache index rows."""

from typing import Any

from by_qa.knowledge_base.infrastructure.storage import StorageLocation


class KnowledgeFetchCacheRepository:
    """Repository for cache index rows backing fetched local files."""

    async def upsert_cache_entry(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        fs_entry_id: int,
        full_path: str,
        source_location: StorageLocation,
        checksum: str | None,
        cache_file_path: str,
        file_size: int | None,
        cache_ttl_seconds: int,
    ) -> dict[str, Any] | None:
        """Insert or refresh one local cache entry."""
        await cursor.execute(
            """
            DELETE FROM knowledge_fetch_cache_index
            WHERE fs_entry_id = %(fs_entry_id)s
               OR cache_file_path = %(cache_file_path)s
            """,
            {
                "knowledge_base_id": knowledge_base_id,
                "fs_entry_id": fs_entry_id,
                "full_path": full_path,
                "bucket_name": source_location.namespace,
                "object_key": source_location.key,
                "checksum": checksum,
                "cache_file_path": cache_file_path,
                "file_size": file_size,
                "cache_ttl_seconds": cache_ttl_seconds,
            },
        )
        await cursor.execute(
            """
            INSERT INTO knowledge_fetch_cache_index (
                knowledge_base_id,
                fs_entry_id,
                full_path,
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
                %(full_path)s,
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
                "full_path": full_path,
                "bucket_name": source_location.namespace,
                "object_key": source_location.key,
                "checksum": checksum,
                "cache_file_path": cache_file_path,
                "file_size": file_size,
                "cache_ttl_seconds": cache_ttl_seconds,
            },
        )
        await cursor.execute(
            """
            SELECT kid
            FROM knowledge_fetch_cache_index
            WHERE fs_entry_id = %(fs_entry_id)s
            """,
            {"fs_entry_id": fs_entry_id},
        )
        return await cursor.fetchone()

    async def get_by_fs_entry_id(
        self, cursor: Any, *, fs_entry_id: int
    ) -> dict[str, Any] | None:
        """Fetch the cache row for one file node."""
        await cursor.execute(
            """
            SELECT *
            FROM knowledge_fetch_cache_index
            WHERE fs_entry_id = %(fs_entry_id)s
            """,
            {"fs_entry_id": fs_entry_id},
        )
        return await cursor.fetchone()

    async def touch_cache_entry(
        self, cursor: Any, *, cache_entry_id: int, cache_ttl_seconds: int
    ) -> None:
        """Extend one cache row after a successful cache hit."""
        await cursor.execute(
            """
            UPDATE knowledge_fetch_cache_index
            SET last_accessed_at = NOW(),
                expires_at = NOW() + (%(cache_ttl_seconds)s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE kid = %(cache_entry_id)s
            """,
            {"cache_entry_id": cache_entry_id, "cache_ttl_seconds": cache_ttl_seconds},
        )

    async def mark_expired_ready_entries_as_evicting(
        self, cursor: Any, *, batch_size: int
    ) -> None:
        """Promote a bounded batch of expired READY rows into EVICTING."""
        await cursor.execute(
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

    async def list_cleanup_candidates(
        self, cursor: Any, *, batch_size: int
    ) -> list[dict[str, Any]]:
        """Load rows that should be retried by the cleanup worker."""
        await cursor.execute(
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
        return list(await cursor.fetchall())

    async def delete_cache_entry(self, cursor: Any, *, cache_entry_id: int) -> None:
        """Delete one cache row after successful eviction."""
        await cursor.execute(
            """
            DELETE FROM knowledge_fetch_cache_index
            WHERE kid = %(cache_entry_id)s
            """,
            {"cache_entry_id": cache_entry_id},
        )

    async def mark_cache_entry_error(
        self, cursor: Any, *, cache_entry_id: int, error: str
    ) -> None:
        """Persist a failed eviction attempt for retry in the next cleanup cycle."""
        await cursor.execute(
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

    async def delete_cache_entries_for_fs_entry_ids(
        self, cursor, *, fs_entry_ids: list[int]
    ) -> None:
        if not fs_entry_ids:
            return
        await cursor.execute(
            "DELETE FROM knowledge_fetch_cache_index WHERE fs_entry_id = ANY(%(fs_entry_ids)s)",
            {"fs_entry_ids": list(fs_entry_ids)},
        )
